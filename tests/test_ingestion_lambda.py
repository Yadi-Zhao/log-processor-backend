"""
Unit tests for the ingestion Lambda function.

This module tests the API request handling, input validation, and message queuing
functionality of the ingestion endpoint.
"""

import json
import os
import pytest
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime

# CRITICAL: Set sys.path BEFORE any lambda_function imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lambda/ingestion'))

# CRITICAL: Mock boto3 BEFORE importing lambda_function
# This ensures the module-level boto3.client() call uses our mock
import boto3
original_boto3_client = boto3.client

def mock_boto3_client(*args, **kwargs):
    """Mock boto3.client to return a MagicMock"""
    mock = MagicMock()
    mock.send_message.return_value = {'MessageId': 'test-message-id'}
    return mock

# Patch boto3.client globally before any imports
boto3.client = mock_boto3_client

# NOW we can safely import lambda_function
import lambda_function

# Restore original after import (optional, but cleaner)
boto3.client = original_boto3_client


@pytest.fixture(autouse=True)
def mock_environment():
    """Set up test environment variables."""
    with patch.dict(os.environ, {
        'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue',
        'AWS_DEFAULT_REGION': 'us-east-1'
    }):
        yield


@pytest.fixture
def mock_sqs_client():
    """Create a mocked SQS client."""
    with patch.object(lambda_function, 'sqs') as mock_sqs:
        mock_sqs.send_message.return_value = {
            'MessageId': 'msg-12345'
        }
        yield mock_sqs


class TestJSONPayloadHandling:
    """Tests for JSON format request handling."""
    
    def test_valid_json_request_with_all_fields(self, mock_sqs_client):
        """
        Verify that a well-formed JSON request with all fields is processed correctly.
        The function should accept the request and queue it for processing.
        """
        event = {
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                'tenant_id': 'acme-corp',
                'log_id': 'log-001',
                'text': 'Application started successfully'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 202
        body = json.loads(response['body'])
        assert body['message'] == 'Accepted'
        assert body['tenant_id'] == 'acme-corp'
        assert body['log_id'] == 'log-001'
        
        # Verify SQS message was sent
        mock_sqs_client.send_message.assert_called_once()
        call_args = mock_sqs_client.send_message.call_args
        message_body = json.loads(call_args[1]['MessageBody'])
        assert message_body['tenant_id'] == 'acme-corp'
        assert message_body['text'] == 'Application started successfully'
    
    def test_json_request_without_log_id(self, mock_sqs_client):
        """
        Test that log_id is auto-generated when not provided.
        This allows clients to omit log_id and let the system generate one.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'beta-inc',
                'text': 'User login attempt'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 202
        body = json.loads(response['body'])
        # Should have generated a UUID
        assert 'log_id' in body
        assert len(body['log_id']) == 36  # UUID format
    
    def test_malformed_json_returns_400(self, mock_sqs_client):
        """
        Ensure that invalid JSON syntax is rejected with appropriate error.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': '{"tenant_id": "test", invalid json here}'
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body
        assert body['error'] == 'Invalid JSON'


class TestPlainTextPayloadHandling:
    """Tests for plain text format request handling."""
    
    def test_valid_text_request(self, mock_sqs_client):
        """
        Verify plain text requests are accepted when tenant ID is in headers.
        """
        event = {
            'headers': {
                'Content-Type': 'text/plain',
                'X-Tenant-Id': 'gamma-systems'
            },
            'body': 'Database connection timeout after 30 seconds'
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 202
        body = json.loads(response['body'])
        assert body['tenant_id'] == 'gamma-systems'
        assert 'log_id' in body  # Auto-generated
    
    def test_text_request_with_mixed_case_headers(self, mock_sqs_client):
        """
        Ensure header parsing is case-insensitive.
        HTTP headers should be treated case-insensitively per RFC 2616.
        """
        event = {
            'headers': {
                'content-TYPE': 'text/plain',  # Mixed case
                'x-TENANT-id': 'test-tenant'   # Mixed case
            },
            'body': 'System health check passed'
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 202


class TestInputValidation:
    """Tests for input validation and error handling."""
    
    def test_missing_tenant_id_in_json(self, mock_sqs_client):
        """
        Request must be rejected if tenant_id is not provided.
        Tenant isolation requires explicit tenant identification.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'text': 'Log message without tenant'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body
        assert 'tenant_id' in body['error'].lower()
    
    def test_missing_text_field(self, mock_sqs_client):
        """
        Request must include actual log content.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'delta-corp'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body
    
    def test_empty_tenant_id_is_rejected(self, mock_sqs_client):
        """
        Empty string should not be accepted as a valid tenant ID.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': '',
                'text': 'Some log message'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 400
    
    def test_unsupported_content_type(self, mock_sqs_client):
        """
        Only JSON and plain text are supported content types.
        """
        event = {
            'headers': {'Content-Type': 'application/xml'},
            'body': '<log><text>XML format</text></log>'
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'Unsupported Content-Type' in body['error']


class TestMessageQueueing:
    """Tests for SQS message queuing functionality."""
    
    def test_message_attributes_include_tenant_id(self, mock_sqs_client):
        """
        Tenant ID should be included in message attributes for filtering.
        This enables potential SQS-level tenant-based routing in the future.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'epsilon-labs',
                'text': 'Performance metrics logged'
            })
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_sqs_client.send_message.call_args
        message_attrs = call_args[1]['MessageAttributes']
        assert 'tenant_id' in message_attrs
        assert message_attrs['tenant_id']['StringValue'] == 'epsilon-labs'
    
    def test_queued_message_includes_timestamp(self, mock_sqs_client):
        """
        Each message should be timestamped for audit trail purposes.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'zeta-co',
                'text': 'Transaction completed'
            })
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_sqs_client.send_message.call_args
        message_body = json.loads(call_args[1]['MessageBody'])
        assert 'timestamp' in message_body
        # Verify timestamp is in ISO format
        datetime.fromisoformat(message_body['timestamp'])


class TestResponseFormat:
    """Tests for API response format and headers."""
    
    def test_response_includes_cors_headers(self, mock_sqs_client):
        """
        CORS headers should be included to support web clients.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'web-client',
                'text': 'Browser request'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert 'Access-Control-Allow-Origin' in response['headers']
    
    def test_content_type_is_json(self, mock_sqs_client):
        """
        All responses should indicate JSON content type.
        """
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'content-test',
                'text': 'Test message'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['headers']['Content-Type'] == 'application/json'


class TestErrorHandling:
    """Tests for error scenarios and exception handling."""
    
    def test_sqs_failure_returns_500(self, mock_sqs_client):
        """
        If SQS is unavailable, return 500 to indicate server-side issue.
        """
        # Simulate SQS failure
        mock_sqs_client.send_message.side_effect = Exception('SQS service unavailable')
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'error-test',
                'text': 'This should fail'
            })
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert 'error' in body
    
    def test_graceful_handling_of_missing_headers(self, mock_sqs_client):
        """
        Function should handle requests with missing headers dictionary.
        """
        event = {
            'body': json.dumps({
                'tenant_id': 'no-headers',
                'text': 'Request without headers'
            })
        }
        
        # Should not crash, even though headers are missing
        response = lambda_function.lambda_handler(event, None)
        
        # Will likely fail validation, but should return valid response
        assert 'statusCode' in response
        assert 'body' in response