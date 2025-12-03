"""
Shared pytest fixtures and configuration.

This file contains fixtures that are shared across multiple test modules.
"""

import pytest
import os
from unittest.mock import patch, MagicMock


@pytest.fixture
def sample_sqs_event():
    """
    Generate a sample SQS event structure.
    Useful for testing worker Lambda with various message payloads.
    """
    def _create_event(messages):
        """
        Args:
            messages: List of message dictionaries to include in the event
        
        Returns:
            Dict representing an SQS Lambda event
        """
        import json
        
        if not isinstance(messages, list):
            messages = [messages]
        
        return {
            'Records': [
                {
                    'messageId': f'msg-{i}',
                    'receiptHandle': f'receipt-{i}',
                    'body': json.dumps(msg),
                    'attributes': {
                        'ApproximateReceiveCount': '1',
                        'SentTimestamp': '1609459200000',
                        'SenderId': 'AIDAI23EXAMPLE',
                        'ApproximateFirstReceiveTimestamp': '1609459200000'
                    },
                    'messageAttributes': {},
                    'md5OfBody': 'abc123',
                    'eventSource': 'aws:sqs',
                    'eventSourceARN': 'arn:aws:sqs:us-east-1:123456789:test-queue',
                    'awsRegion': 'us-east-1'
                }
                for i, msg in enumerate(messages)
            ]
        }
    
    return _create_event


@pytest.fixture
def sample_api_gateway_event():
    """
    Generate a sample API Gateway event structure.
    Useful for testing ingestion Lambda with various request formats.
    """
    def _create_event(body, headers=None, method='POST', path='/ingest'):
        """
        Args:
            body: Request body (string)
            headers: Request headers (dict)
            method: HTTP method
            path: Request path
        
        Returns:
            Dict representing an API Gateway Lambda proxy event
        """
        if headers is None:
            headers = {}
        
        return {
            'resource': path,
            'path': path,
            'httpMethod': method,
            'headers': headers,
            'body': body,
            'isBase64Encoded': False,
            'requestContext': {
                'accountId': '123456789012',
                'apiId': 'api123',
                'protocol': 'HTTP/1.1',
                'httpMethod': method,
                'path': path,
                'stage': 'test',
                'requestId': 'test-request-id',
                'requestTime': '01/Jan/2024:00:00:00 +0000',
                'requestTimeEpoch': 1609459200000,
                'identity': {
                    'sourceIp': '127.0.0.1',
                    'userAgent': 'test-agent'
                }
            }
        }
    
    return _create_event


@pytest.fixture
def mock_aws_credentials():
    """
    Mock AWS credentials to prevent actual AWS calls during tests.
    """
    with patch.dict(os.environ, {
        'AWS_ACCESS_KEY_ID': 'testing',
        'AWS_SECRET_ACCESS_KEY': 'testing',
        'AWS_SECURITY_TOKEN': 'testing',
        'AWS_SESSION_TOKEN': 'testing',
        'AWS_DEFAULT_REGION': 'us-east-1'
    }):
        yield


@pytest.fixture
def sample_log_message():
    """
    Generate a sample log message for testing.
    """
    def _create_message(
        tenant_id='test-tenant',
        log_id='log-001',
        text='Sample log message',
        source='json',
        timestamp='2024-01-15T10:30:00'
    ):
        return {
            'tenant_id': tenant_id,
            'log_id': log_id,
            'text': text,
            'source': source,
            'timestamp': timestamp
        }
    
    return _create_message