"""
Unit tests for the worker Lambda function.

This module tests message processing, PII redaction, and data persistence
with proper tenant isolation.
"""

import json
import os
import pytest
import sys
from unittest.mock import patch, MagicMock
from decimal import Decimal

# CRITICAL: Remove cached lambda_function from ingestion tests
if 'lambda_function' in sys.modules:
    del sys.modules['lambda_function']

# CRITICAL: Set sys.path BEFORE any lambda_function imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lambda/worker'))

# CRITICAL: Mock boto3 BEFORE importing lambda_function
import boto3
original_boto3_resource = boto3.resource

def mock_boto3_resource(*args, **kwargs):
    """Mock boto3.resource to return a MagicMock with Table"""
    mock_dynamodb = MagicMock()
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_dynamodb.Table.return_value = mock_table
    return mock_dynamodb

# Patch boto3.resource globally before any imports
boto3.resource = mock_boto3_resource

# NOW we can safely import lambda_function
import lambda_function

# Restore original after import
boto3.resource = original_boto3_resource


@pytest.fixture(autouse=True)
def mock_environment():
    """Set up test environment variables."""
    with patch.dict(os.environ, {
        'DYNAMODB_TABLE': 'test-logs-table',
        'AWS_DEFAULT_REGION': 'us-east-1'
    }):
        yield


@pytest.fixture
def mock_dynamodb():
    """Create a mocked DynamoDB table resource."""
    with patch.object(lambda_function, 'table') as mock_table:
        mock_table.put_item.return_value = {}
        yield mock_table


@pytest.fixture
def mock_sleep():
    """Mock time.sleep to speed up tests."""
    with patch('lambda_function.time.sleep') as mock:
        yield mock


class TestMessageProcessing:
    """Tests for basic message processing functionality."""
    
    def test_single_message_processing(self, mock_dynamodb, mock_sleep):
        """
        Verify that a single SQS message is processed and stored correctly.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'tenant-alpha',
                        'log_id': 'log-12345',
                        'text': 'User login successful',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 200
        mock_dynamodb.put_item.assert_called_once()
    
    def test_batch_message_processing(self, mock_dynamodb, mock_sleep):
        """
        Worker should handle multiple messages in a single invocation.
        SQS can deliver up to 10 messages per batch.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': f'tenant-{i}',
                        'log_id': f'log-{i}',
                        'text': f'Message {i}',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
                for i in range(5)
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 200
        assert mock_dynamodb.put_item.call_count == 5
    
    def test_processing_time_calculation(self, mock_dynamodb, mock_sleep):
        """
        Processing time should be calculated at 0.05s per character.
        This simulates CPU-intensive log analysis.
        """
        # 20 characters = 1.0 second processing time
        test_text = 'a' * 20
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'perf-test',
                        'log_id': 'perf-001',
                        'text': test_text,
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        # Verify sleep was called with correct duration
        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration == 1.0  # 20 chars * 0.05s
    
    def test_error_propagation_for_retry_logic(self, mock_dynamodb, mock_sleep):
        """
        If processing fails, exception should propagate to trigger SQS retry.
        Failed messages will be retried or sent to DLQ based on queue config.
        """
        mock_dynamodb.put_item.side_effect = Exception('DynamoDB write failed')
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'error-tenant',
                        'log_id': 'error-log',
                        'text': 'This will fail',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        with pytest.raises(Exception):
            lambda_function.lambda_handler(event, None)


class TestTenantIsolation:
    """Tests for multi-tenant data isolation."""
    
    def test_composite_key_structure(self, mock_dynamodb, mock_sleep):
        """
        Verify that PK and SK follow the tenant isolation pattern.
        PK format: TENANT#{tenant_id}
        SK format: LOG#{log_id}
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'acme-corp',
                        'log_id': 'log-999',
                        'text': 'Test message',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        assert item['PK'] == 'TENANT#acme-corp'
        assert item['SK'] == 'LOG#log-999'
    
    def test_tenant_id_stored_separately(self, mock_dynamodb, mock_sleep):
        """
        Tenant ID should be stored as a separate attribute for querying.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'beta-systems',
                        'log_id': 'log-555',
                        'text': 'Audit log entry',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        assert item['tenant_id'] == 'beta-systems'
        assert item['log_id'] == 'log-555'


class TestDataPersistence:
    """Tests for DynamoDB data storage."""
    
    def test_all_required_fields_stored(self, mock_dynamodb, mock_sleep):
        """
        Verify that all message fields are persisted to DynamoDB.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'gamma-inc',
                        'log_id': 'log-777',
                        'text': 'Transaction completed',
                        'source': 'text',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        # Verify all required fields are present
        assert 'PK' in item
        assert 'SK' in item
        assert 'tenant_id' in item
        assert 'log_id' in item
        assert 'source' in item
        assert 'original_text' in item
        assert 'modified_data' in item
        assert 'ingested_at' in item
        assert 'processed_at' in item
        assert 'text_length' in item
        assert 'processing_time_sec' in item
    
    def test_timestamps_are_stored(self, mock_dynamodb, mock_sleep):
        """
        Both ingestion and processing timestamps should be recorded.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'time-test',
                        'log_id': 'log-time',
                        'text': 'Timestamp test',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        assert item['ingested_at'] == '2024-01-15T10:30:00'
        assert 'processed_at' in item
    
    def test_text_length_calculated(self, mock_dynamodb, mock_sleep):
        """
        Text length should be stored for analytics.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'length-test',
                        'log_id': 'log-len',
                        'text': 'abcde',  # 5 characters
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        assert item['text_length'] == 5
    
    def test_processing_time_stored_as_decimal(self, mock_dynamodb, mock_sleep):
        """
        Processing time should be stored as Decimal for DynamoDB compatibility.
        """
        # 2 characters = 0.1 seconds
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'decimal-test',
                        'log_id': 'log-dec',
                        'text': 'ab',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        assert isinstance(item['processing_time_sec'], Decimal)
        assert item['processing_time_sec'] == Decimal('0.1')


class TestPIIRedaction:
    """
    Tests for PII (Personally Identifiable Information) redaction.
    Critical for compliance with privacy regulations like GDPR and CCPA.
    """
    
    def test_redact_seven_digit_phone(self):
        """
        Phone numbers in 555-1234 format should be redacted.
        """
        text = "Please call me at 555-1234 for details"
        result = lambda_function.redact_sensitive_data(text)
        
        assert result == "Please call me at [REDACTED] for details"
        assert "555-1234" not in result
    
    def test_redact_ten_digit_phone(self):
        """
        Phone numbers in 123-456-7890 format should be redacted.
        """
        text = "Contact: 123-456-7890"
        result = lambda_function.redact_sensitive_data(text)
        
        assert result == "Contact: [REDACTED]"
        assert "123-456-7890" not in result
    
    def test_redact_multiple_phone_numbers(self):
        """
        All phone numbers in text should be redacted independently.
        """
        text = "Call 555-1234 or 888-999-0000 for support"
        result = lambda_function.redact_sensitive_data(text)
        
        assert result == "Call [REDACTED] or [REDACTED] for support"
    
    def test_redact_ip_addresses(self):
        """
        IPv4 addresses should be redacted for security.
        """
        text = "Server IP: 192.168.1.1, Gateway: 10.0.0.1"
        result = lambda_function.redact_sensitive_data(text)
        
        assert "[IP_REDACTED]" in result
        assert "192.168.1.1" not in result
        assert "10.0.0.1" not in result
    
    def test_redact_email_addresses(self):
        """
        Email addresses should be redacted to protect user identity.
        """
        text = "Contact john.doe@example.com or support@company.org"
        result = lambda_function.redact_sensitive_data(text)
        
        assert "[EMAIL_REDACTED]" in result
        assert "john.doe@example.com" not in result
        assert "support@company.org" not in result
    
    def test_redact_mixed_pii_types(self):
        """
        Complex logs may contain multiple PII types that all need redaction.
        """
        text = "User alice@test.com from IP 172.16.0.5 called 555-9876"
        result = lambda_function.redact_sensitive_data(text)
        
        # All PII should be redacted
        assert "alice@test.com" not in result
        assert "172.16.0.5" not in result
        assert "555-9876" not in result
        
        # Redaction markers should be present
        assert "[EMAIL_REDACTED]" in result
        assert "[IP_REDACTED]" in result
        assert "[REDACTED]" in result
    
    def test_preserve_non_pii_content(self):
        """
        Non-sensitive content should remain unchanged.
        """
        text = "Application started successfully at 2024-01-15 10:30:00"
        result = lambda_function.redact_sensitive_data(text)
        
        assert result == text
    
    def test_partial_phone_number_not_redacted(self):
        """
        Incomplete patterns should not trigger false positives.
        For example, order IDs or other numbers that aren't full phone numbers.
        """
        # These should NOT be redacted as they don't match full phone patterns
        text = "Order ID: 555-12, Confirmation: 888-99"
        result = lambda_function.redact_sensitive_data(text)
        
        # Should remain unchanged since these aren't valid phone number patterns
        assert "555-12" in result
        assert "888-99" in result
    
    def test_redaction_applied_before_storage(self, mock_dynamodb, mock_sleep):
        """
        Integration test: verify redacted data is what gets stored.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'security-test',
                        'log_id': 'log-secure',
                        'text': 'User john@example.com logged in from 192.168.1.1',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        lambda_function.lambda_handler(event, None)
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        
        # Original text preserved for audit
        assert 'john@example.com' in item['original_text']
        
        # Modified data has PII redacted
        assert 'john@example.com' not in item['modified_data']
        assert '[EMAIL_REDACTED]' in item['modified_data']
        assert '[IP_REDACTED]' in item['modified_data']


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_empty_text_processing(self, mock_dynamodb, mock_sleep):
        """
        Handle empty log text gracefully.
        """
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'edge-test',
                        'log_id': 'log-empty',
                        'text': '',
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        # Should process successfully even with empty text
        assert response['statusCode'] == 200
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        assert item['text_length'] == 0
        assert item['processing_time_sec'] == Decimal('0')
    
    def test_very_long_text(self, mock_dynamodb, mock_sleep):
        """
        Ensure system can handle large log entries.
        """
        # Simulate a large log entry (1000 characters)
        large_text = 'x' * 1000
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'large-test',
                        'log_id': 'log-large',
                        'text': large_text,
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 200
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        assert item['text_length'] == 1000
        # 1000 chars * 0.05s = 50.0 seconds
        assert item['processing_time_sec'] == Decimal('50.0')
    
    def test_special_characters_in_text(self, mock_dynamodb, mock_sleep):
        """
        Special characters and Unicode should be handled correctly.
        """
        text_with_special_chars = "Error: ñoño™ — \"quotes\" & <tags> 中文"
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'unicode-test',
                        'log_id': 'log-unicode',
                        'text': text_with_special_chars,
                        'source': 'json',
                        'timestamp': '2024-01-15T10:30:00'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 200
        
        call_args = mock_dynamodb.put_item.call_args
        item = call_args[1]['Item']
        assert item['original_text'] == text_with_special_chars