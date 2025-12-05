import json
import boto3
import os
import uuid
import re
from datetime import datetime, UTC
from botocore.exceptions import ClientError

sqs = boto3.client('sqs')
QUEUE_URL = os.environ.get('SQS_QUEUE_URL')

MAX_CHAR_LIMIT = 17000
MAX_TENANT_ID_LENGTH = 100
MAX_LOG_ID_LENGTH = 100
TENANT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

def lambda_handler(event, context):
    """
    Ingestion endpoint handler.
    Accepts both JSON and plain text formats, normalizes them, and queues for processing.
    """
    
    try:
        headers = event.get('headers', {})
        headers_lower = {k.lower(): v for k, v in headers.items()}
        content_type = headers_lower.get('content-type', '')
        body = event.get('body', '')
        
        # Handle JSON payload
        if 'application/json' in content_type:
            try:
                data = json.loads(body)

                if not isinstance(data, dict):
                    return {
                        'statusCode': 400,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps({
                            'error': 'Invalid JSON structure',
                            'detail': 'Expected JSON object, not array'
                        })
                    }

                tenant_id = data.get('tenant_id')
                log_id = data.get('log_id', str(uuid.uuid4()))
                text = data.get('text')
                source = 'json'
            except json.JSONDecodeError:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'error': 'Invalid JSON'})
                }
        
        # Handle plain text payload
        elif 'text/plain' in content_type:
            tenant_id = headers_lower.get('x-tenant-id')
            log_id = str(uuid.uuid4())
            text = body
            source = 'text'
        
        else:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Unsupported Content-Type'})
            }
        
        # Validate required fields
        if not tenant_id or not text:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Missing tenant_id or text'})
            }
        
        if not isinstance(tenant_id, str):
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Validation failed',
                    'detail': 'tenant_id must be a string'
                })
            }
        
        if not isinstance(text, str):
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Validation failed',
                    'detail': 'text must be a string'
                })
            }
        
        if log_id:
            if not isinstance(log_id, str):
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'Validation failed',
                        'detail': 'log_id must be a string'
                    })
                }
            if len(log_id) > MAX_LOG_ID_LENGTH:
                print(f"✗ log_id too long: {len(log_id)} chars")
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'Validation failed',
                        'detail': f'log_id must not exceed {MAX_LOG_ID_LENGTH} characters (got {len(log_id)})'
                    })
                }
        
        if len(tenant_id) > MAX_TENANT_ID_LENGTH:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Validation failed',
                    'detail': f'tenant_id exceeds {MAX_TENANT_ID_LENGTH} characters'
                })
            }
        
        
        if not TENANT_ID_PATTERN.match(tenant_id):
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Validation failed',
                    'detail': 'tenant_id can only contain letters, numbers, hyphens, and underscores'
                })
            }

        
        if len(text) > MAX_CHAR_LIMIT:
            print(f"Request rejected: text length {len(text)} exceeds limit {MAX_CHAR_LIMIT}")
            return {
                'statusCode': 413, # 413 Payload Too Large
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': f'Payload text exceeds maximum allowed characters ({MAX_CHAR_LIMIT})'})
            }
        
        # Normalize into unified message format
        message = {
            'tenant_id': tenant_id,
            'log_id': log_id,
            'text': text,
            'source': source,
            'timestamp': datetime.now(UTC).isoformat()
        }
        
        # Queue message for async processing
        try:
            response = sqs.send_message(
                QueueUrl=QUEUE_URL,
                MessageBody=json.dumps(message),
                MessageAttributes={
                    'tenant_id': {
                        'StringValue': tenant_id,
                        'DataType': 'String'
                    }
                }
            )

            print(f"Queued message {response['MessageId']} for tenant {tenant_id}")

            # Return immediately with 202 Accepted
            return {
                'statusCode': 202,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'message': 'Accepted',
                    'log_id': log_id,
                    'tenant_id': tenant_id
                })
            }

        except ClientError as e:
            print(f"SQS Client Error: {e}")
            return {
                'statusCode': 503,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Service Unavailable: Failed to queue message'})
            }
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Internal server error'})
        }