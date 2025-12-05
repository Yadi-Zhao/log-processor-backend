import json
import boto3
import os
import time
import re
from datetime import datetime, UTC
from decimal import Decimal
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE')
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, context):
    """
    Worker handler for processing queued messages.
    Implements idempotency to handle duplicate SQS messages gracefully.
    Simulates heavy processing and stores results with tenant isolation.
    """
    
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    for record in event['Records']:
        try:
            message = json.loads(record['body'])
            
            tenant_id = message['tenant_id']
            log_id = message['log_id']
            text = message['text']
            source = message['source']
            ingestion_timestamp = message['timestamp']
            
            pk = f'TENANT#{tenant_id}'
            sk = f'LOG#{log_id}'
            
            # Simulate processing time: 0.05s per character
            processing_time = len(text) * 0.05
            print(f"Processing {log_id} for tenant {tenant_id}, estimated {processing_time:.2f}s")
            time.sleep(processing_time)
            
            # Apply data redaction
            modified_text = redact_sensitive_data(text)
            
            # Store with tenant isolation and idempotent write
            try:
                table.put_item(
                    Item={
                        'PK': pk,
                        'SK': sk,
                        'tenant_id': tenant_id,
                        'log_id': log_id,
                        'source': source,
                        'original_text': text,
                        'modified_data': modified_text,
                        'ingested_at': ingestion_timestamp,
                        'processed_at': datetime.now(UTC).isoformat(),
                        'text_length': len(text),
                        'processing_time_sec': Decimal(str(round(processing_time, 2)))
                    },
                    # Conditional expression ensures idempotency
                    ConditionExpression='attribute_not_exists(PK) AND attribute_not_exists(SK)'
                )
                
                print(f"✓ Successfully processed log {log_id} for tenant {tenant_id}")
                processed_count += 1
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    # This is a duplicate message - log already exists
                    print(f"⊘ Duplicate message detected: log {log_id} for tenant {tenant_id} already processed, skipping")
                    skipped_count += 1
                    # Don't raise - continue processing other messages
                else:
                    # Other DynamoDB error (throttling, etc.) - re-raise
                    print(f"✗ DynamoDB error for log {log_id}: {e.response['Error']['Code']}")
                    error_count += 1
                    raise
            
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON in message body: {str(e)}")
            error_count += 1
            # Don't raise - malformed message won't succeed on retry
            continue
            
        except KeyError as e:
            print(f"✗ Missing required field in message: {str(e)}")
            error_count += 1
            # Don't raise - malformed message won't succeed on retry
            continue
            
        except Exception as e:
            print(f"✗ Unexpected processing error: {str(e)}")
            error_count += 1
            raise  # Let SQS handle retry logic for unexpected errors
    
    result = {
        'processed': processed_count,
        'skipped_duplicates': skipped_count,
        'errors': error_count,
        'total': len(event['Records'])
    }
    
    print(f"Batch processing complete: {json.dumps(result)}")
    
    return {
        'statusCode': 200,
        'body': json.dumps(result)
    }


def redact_sensitive_data(text):
    """
    Remove PII from log text.
    Redacts phone numbers, IP addresses, and email addresses.
    """
    
    # Redact 10-digit phone numbers FIRST (before 7-digit)
    text = re.sub(r'\d{3}-\d{3}-\d{4}', '[REDACTED]', text)
    
    # Then redact 7-digit phone numbers
    text = re.sub(r'\d{3}-\d{4}', '[REDACTED]', text)
    
    # Redact IP addresses
    text = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[IP_REDACTED]', text)
    
    # Redact email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', text)
    
    return text