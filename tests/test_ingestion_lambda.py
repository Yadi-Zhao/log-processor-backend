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
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lambda/ingestion'))

# Mock environment variables before importing the lambda function
@pytest.fixture(autouse=True)
def mock_environment():
    """Set up test environment variables."""
    with patch.dict(os.environ, {
        'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue'
    }):
        yield


@pytest.fixture
def mock_sqs_client():
    """Create a mocked SQS client."""
    with patch('boto3.client') as mock_client:
        mock_sqs = MagicMock()
        mock_client.return_value = mock_sqs
        yield mock_sqs


class TestJSONPayloadHandling:
    """Tests for JSON format request handling."""
    
    def test_valid_json_request_with_all_fields(self, mock_sqs_client):
        """
        Verify that a well-formed JSON request with all fields is processed correctly.
        The function should accept the request and queue it for processing.
        """
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {
            'MessageId': 'msg-12345'
        }
        
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
        
        response = lambda_handler(event, None)
        
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
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-67890'}
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'beta-inc',
                'text': 'User login attempt'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 202
        body = json.loads(response['body'])
        # Should have generated a UUID
        assert 'log_id' in body
        assert len(body['log_id']) == 36  # UUID format
    
    def test_malformed_json_returns_400(self, mock_sqs_client):
        """
        Ensure that invalid JSON syntax is rejected with appropriate error.
        """
        from lambda_function import lambda_handler
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': '{"tenant_id": "test", invalid json here}'
        }
        
        response = lambda_handler(event, None)
        
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
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-text-123'}
        
        event = {
            'headers': {
                'Content-Type': 'text/plain',
                'X-Tenant-Id': 'gamma-systems'
            },
            'body': 'Database connection timeout after 30 seconds'
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 202
        body = json.loads(response['body'])
        assert body['tenant_id'] == 'gamma-systems'
        assert 'log_id' in body  # Auto-generated
    
    def test_text_request_with_mixed_case_headers(self, mock_sqs_client):
        """
        Ensure header parsing is case-insensitive.
        HTTP headers should be treated case-insensitively per RFC 2616.
        """
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-case-test'}
        
        event = {
            'headers': {
                'content-TYPE': 'text/plain',  # Mixed case
                'x-TENANT-id': 'test-tenant'   # Mixed case
            },
            'body': 'System health check passed'
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 202


class TestInputValidation:
    """Tests for input validation and error handling."""
    
    def test_missing_tenant_id_in_json(self, mock_sqs_client):
        """
        Request must be rejected if tenant_id is not provided.
        Tenant isolation requires explicit tenant identification.
        """
        from lambda_function import lambda_handler
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'text': 'Log message without tenant'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body
        assert 'tenant_id' in body['error'].lower()
    
    def test_missing_text_field(self, mock_sqs_client):
        """
        Request must include actual log content.
        """
        from lambda_function import lambda_handler
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'delta-corp'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body
    
    def test_empty_tenant_id_is_rejected(self, mock_sqs_client):
        """
        Empty string should not be accepted as a valid tenant ID.
        """
        from lambda_function import lambda_handler
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': '',
                'text': 'Some log message'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 400
    
    def test_unsupported_content_type(self, mock_sqs_client):
        """
        Only JSON and plain text are supported content types.
        """
        from lambda_function import lambda_handler
        
        event = {
            'headers': {'Content-Type': 'application/xml'},
            'body': '<log><text>XML format</text></log>'
        }
        
        response = lambda_handler(event, None)
        
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
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-attr-test'}
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'epsilon-labs',
                'text': 'Performance metrics logged'
            })
        }
        
        lambda_handler(event, None)
        
        call_args = mock_sqs_client.send_message.call_args
        message_attrs = call_args[1]['MessageAttributes']
        assert 'tenant_id' in message_attrs
        assert message_attrs['tenant_id']['StringValue'] == 'epsilon-labs'
    
    def test_queued_message_includes_timestamp(self, mock_sqs_client):
        """
        Each message should be timestamped for audit trail purposes.
        """
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-time-test'}
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'zeta-co',
                'text': 'Transaction completed'
            })
        }
        
        lambda_handler(event, None)
        
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
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-cors-test'}
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'web-client',
                'text': 'Browser request'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert 'Access-Control-Allow-Origin' in response['headers']
    
    def test_content_type_is_json(self, mock_sqs_client):
        """
        All responses should indicate JSON content type.
        """
        from lambda_function import lambda_handler
        
        mock_sqs_client.send_message.return_value = {'MessageId': 'msg-ct-test'}
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'content-test',
                'text': 'Test message'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['headers']['Content-Type'] == 'application/json'


class TestErrorHandling:
    """Tests for error scenarios and exception handling."""
    
    def test_sqs_failure_returns_500(self, mock_sqs_client):
        """
        If SQS is unavailable, return 500 to indicate server-side issue.
        """
        from lambda_function import lambda_handler
        
        # Simulate SQS failure
        mock_sqs_client.send_message.side_effect = Exception('SQS service unavailable')
        
        event = {
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'tenant_id': 'error-test',
                'text': 'This should fail'
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert 'error' in body
    
    def test_graceful_handling_of_missing_headers(self, mock_sqs_client):
        """
        Function should handle requests with missing headers dictionary.
        """
        from lambda_function import lambda_handler
        
        event = {
            'body': json.dumps({
                'tenant_id': 'no-headers',
                'text': 'Request without headers'
            })
        }
        
        # Should not crash, even though headers are missing
        response = lambda_handler(event, None)
        
        # Will likely fail validation, but should return valid response
        assert 'statusCode' in response
        assert 'body' in response