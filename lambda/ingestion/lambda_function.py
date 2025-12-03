import json
import boto3
import os
import uuid
from datetime import datetime, UTC

sqs = boto3.client('sqs') 
QUEUE_URL = os.environ.get('SQS_QUEUE_URL')

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
        
        # Normalize into unified message format
        message = {
            'tenant_id': tenant_id,
            'log_id': log_id,
            'text': text,
            'source': source,
            'timestamp': datetime.now(UTC).isoformat()
        }
        
        # Queue message for async processing
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
        
        # Return immediately without waiting for processing
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
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Internal server error'})
        }