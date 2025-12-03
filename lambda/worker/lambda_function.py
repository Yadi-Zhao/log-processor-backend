import json
import boto3
import os
import time
import re
from datetime import datetime
from decimal import Decimal

dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
)

TABLE_NAME = os.environ.get('DYNAMODB_TABLE')
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, context):
    """
    Worker handler for processing queued messages.
    Simulates heavy processing and stores results with tenant isolation.
    """
    
    for record in event['Records']:
        try:
            message = json.loads(record['body'])
            
            tenant_id = message['tenant_id']
            log_id = message['log_id']
            text = message['text']
            source = message['source']
            ingestion_timestamp = message['timestamp']
            
            # Simulate processing time: 0.05s per character
            processing_time = len(text) * 0.05
            print(f"Processing {log_id} for {tenant_id}, estimated {processing_time:.2f}s")
            time.sleep(processing_time)
            
            # Apply data redaction
            modified_text = redact_sensitive_data(text)
            
            # Store with tenant isolation using composite primary key
            table.put_item(
                Item={
                    'PK': f'TENANT#{tenant_id}',
                    'SK': f'LOG#{log_id}',
                    'tenant_id': tenant_id,
                    'log_id': log_id,
                    'source': source,
                    'original_text': text,
                    'modified_data': modified_text,
                    'ingested_at': ingestion_timestamp,
                    'processed_at': datetime.utcnow().isoformat(),
                    'text_length': len(text),
                    'processing_time_sec': Decimal(str(round(processing_time, 2)))
                }
            )
            
            print(f"Completed processing {log_id} for {tenant_id}")
            
        except Exception as e:
            print(f"Processing error: {str(e)}")
            raise  # Let SQS handle retry logic
    
    return {
        'statusCode': 200,
        'body': json.dumps(f'Processed {len(event["Records"])} records')
    }

def redact_sensitive_data(text):
    """
    Remove PII from log text.
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