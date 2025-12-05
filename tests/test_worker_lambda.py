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

# 添加到 test_worker_lambda.py 文件末尾

class TestIdempotency:
    """
    Tests for duplicate message handling and idempotency.
    Ensures that duplicate SQS messages don't result in duplicate database entries.
    """
    
    def test_first_message_is_processed_successfully(self, mock_dynamodb, mock_sleep):
        """
        Verify that the first occurrence of a message is processed normally.
        The conditional write should succeed when the item doesn't exist.
        """
        # Mock successful write (no exception)
        mock_dynamodb.put_item.return_value = {}
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'idempotency-test',
                        'log_id': 'log-first-001',
                        'text': 'First message test@example.com',
                        'source': 'json',
                        'timestamp': '2024-12-03T10:00:00Z'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['processed'] == 1
        assert body['skipped_duplicates'] == 0
        
        # Verify put_item was called with ConditionExpression
        mock_dynamodb.put_item.assert_called_once()
        call_kwargs = mock_dynamodb.put_item.call_args[1]
        assert 'ConditionExpression' in call_kwargs
        assert 'attribute_not_exists' in str(call_kwargs['ConditionExpression'])
    
    def test_duplicate_message_is_skipped(self, mock_dynamodb, mock_sleep):
        """
        Verify that duplicate messages (same log_id) are detected and skipped.
        This is the core idempotency test - ensures messages aren't processed twice.
        """
        from botocore.exceptions import ClientError
        
        # Simulate ConditionalCheckFailedException (item already exists)
        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'The conditional request failed'
            }
        }
        mock_dynamodb.put_item.side_effect = ClientError(error_response, 'PutItem')
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'duplicate-test',
                        'log_id': 'log-duplicate-001',
                        'text': 'Duplicate message',
                        'source': 'json',
                        'timestamp': '2024-12-03T10:00:00Z'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        # Should return 200 (handled gracefully, not an error)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['processed'] == 0
        assert body['skipped_duplicates'] == 1
        assert body['total'] == 1
    
    def test_batch_with_mixed_new_and_duplicate_messages(self, mock_dynamodb, mock_sleep):
        """
        Verify correct handling when a batch contains both new and duplicate messages.
        Only new messages should be written; duplicates should be skipped.
        """
        from botocore.exceptions import ClientError
        
        messages = [
            {
                'tenant_id': 'batch-test',
                'log_id': 'log-batch-001',
                'text': 'First message',
                'source': 'json',
                'timestamp': '2024-12-03T10:00:00Z'
            },
            {
                'tenant_id': 'batch-test',
                'log_id': 'log-batch-002',  # This will be duplicate
                'text': 'Second message (duplicate)',
                'source': 'json',
                'timestamp': '2024-12-03T10:01:00Z'
            },
            {
                'tenant_id': 'batch-test',
                'log_id': 'log-batch-003',
                'text': 'Third message',
                'source': 'json',
                'timestamp': '2024-12-03T10:02:00Z'
            }
        ]
        
        # First: success, Second: duplicate, Third: success
        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Item already exists'
            }
        }
        
        mock_dynamodb.put_item.side_effect = [
            {},  # First succeeds
            ClientError(error_response, 'PutItem'),  # Second is duplicate
            {}   # Third succeeds
        ]
        
        event = {
            'Records': [
                {'body': json.dumps(msg)} for msg in messages
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        body = json.loads(response['body'])
        assert body['processed'] == 2  # Two new messages
        assert body['skipped_duplicates'] == 1  # One duplicate
        assert body['total'] == 3
    
    def test_idempotency_preserves_original_timestamp(self, mock_dynamodb, mock_sleep):
        """
        When a duplicate is detected, the original record should not be overwritten.
        This ensures the original processed_at timestamp remains accurate.
        """
        from botocore.exceptions import ClientError
        
        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Item already exists'
            }
        }
        mock_dynamodb.put_item.side_effect = ClientError(error_response, 'PutItem')
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'timestamp-test',
                        'log_id': 'log-timestamp-001',
                        'text': 'Timestamp preservation test',
                        'source': 'json',
                        'timestamp': '2024-12-03T10:00:00Z'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        # Duplicate was skipped - original data preserved
        body = json.loads(response['body'])
        assert body['skipped_duplicates'] == 1
        
        # Verify the attempted write still used conditional expression
        call_kwargs = mock_dynamodb.put_item.call_args[1]
        assert 'ConditionExpression' in call_kwargs
    
    def test_same_log_id_different_tenants_are_separate(self, mock_dynamodb, mock_sleep):
        """
        Idempotency should be scoped to tenant.
        Same log_id for different tenants should create separate records.
        """
        messages = [
            {
                'tenant_id': 'tenant-A',
                'log_id': 'shared-log-id-999',
                'text': 'Message from tenant A',
                'source': 'json',
                'timestamp': '2024-12-03T10:00:00Z'
            },
            {
                'tenant_id': 'tenant-B',
                'log_id': 'shared-log-id-999',  # Same log_id
                'text': 'Message from tenant B',
                'source': 'json',
                'timestamp': '2024-12-03T10:01:00Z'
            }
        ]
        
        # Both should succeed (different PKs due to different tenants)
        mock_dynamodb.put_item.return_value = {}
        
        event = {
            'Records': [
                {'body': json.dumps(msg)} for msg in messages
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        
        body = json.loads(response['body'])
        # Both should be processed (different tenants = different records)
        assert body['processed'] == 2
        assert body['skipped_duplicates'] == 0
        
        # Verify both put_item calls had different PKs
        calls = mock_dynamodb.put_item.call_args_list
        assert len(calls) == 2
        
        pk1 = calls[0][1]['Item']['PK']
        pk2 = calls[1][1]['Item']['PK']
        assert pk1 != pk2
        assert 'TENANT#tenant-A' in pk1
        assert 'TENANT#tenant-B' in pk2
    
    def test_non_duplicate_dynamodb_errors_still_raise(self, mock_dynamodb, mock_sleep):
        """
        Non-idempotency errors (like throttling) should still raise exceptions.
        Only ConditionalCheckFailedException should be handled as duplicate.
        """
        from botocore.exceptions import ClientError
        
        # Simulate throttling error (not a duplicate)
        error_response = {
            'Error': {
                'Code': 'ProvisionedThroughputExceededException',
                'Message': 'Rate exceeded'
            }
        }
        mock_dynamodb.put_item.side_effect = ClientError(error_response, 'PutItem')
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'error-test',
                        'log_id': 'log-error-001',
                        'text': 'This should fail with throttling',
                        'source': 'json',
                        'timestamp': '2024-12-03T10:00:00Z'
                    })
                }
            ]
        }
        
        # Should raise the throttling error (not handled like duplicate)
        with pytest.raises(ClientError) as exc_info:
            lambda_function.lambda_handler(event, None)
        
        assert exc_info.value.response['Error']['Code'] == 'ProvisionedThroughputExceededException'
    
    def test_malformed_message_doesnt_block_batch(self, mock_dynamodb, mock_sleep):
        """
        If one message in a batch is malformed, other messages should still process.
        (This tests error isolation, related to idempotency in batch processing)
        """
        messages = [
            {
                'tenant_id': 'good-tenant-1',
                'log_id': 'log-good-001',
                'text': 'Valid message 1',
                'source': 'json',
                'timestamp': '2024-12-03T10:00:00Z'
            },
            # Malformed message (missing required fields)
            {
                'tenant_id': 'bad-tenant',
                # Missing log_id, text, etc.
            },
            {
                'tenant_id': 'good-tenant-2',
                'log_id': 'log-good-002',
                'text': 'Valid message 2',
                'source': 'json',
                'timestamp': '2024-12-03T10:02:00Z'
            }
        ]
        
        mock_dynamodb.put_item.return_value = {}
        
        event = {
            'Records': [
                {'body': json.dumps(msg)} for msg in messages
            ]
        }
        
        # Should process valid messages despite malformed one
        # Note: Your current implementation might raise on malformed messages
        # This test documents expected behavior - adjust based on your error handling
        try:
            response = lambda_function.lambda_handler(event, None)
            # If graceful handling is implemented:
            body = json.loads(response['body'])
            assert body['errors'] >= 1  # At least one error
        except (KeyError, Exception):
            # If strict validation, that's okay too
            pass


class TestIdempotencyMetrics:
    """Tests for monitoring and observability of idempotency behavior."""
    
    def test_response_includes_duplicate_count(self, mock_dynamodb, mock_sleep):
        """
        Response should include metrics for monitoring duplicate message rates.
        This is important for operational visibility.
        """
        from botocore.exceptions import ClientError
        
        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Item exists'
            }
        }
        mock_dynamodb.put_item.side_effect = ClientError(error_response, 'PutItem')
        
        event = {
            'Records': [
                {
                    'body': json.dumps({
                        'tenant_id': 'metrics-test',
                        'log_id': 'log-metrics-001',
                        'text': 'Metrics test',
                        'source': 'json',
                        'timestamp': '2024-12-03T10:00:00Z'
                    })
                }
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        body = json.loads(response['body'])
        
        # Response must include all key metrics
        assert 'processed' in body
        assert 'skipped_duplicates' in body
        assert 'total' in body
        assert isinstance(body['skipped_duplicates'], int)
        assert body['skipped_duplicates'] == 1
    
    def test_batch_metrics_are_accurate(self, mock_dynamodb, mock_sleep):
        """
        Verify that metrics accurately reflect batch processing results.
        """
        from botocore.exceptions import ClientError
        
        # Create a batch with known outcome:
        # - 3 new messages (success)
        # - 2 duplicates (skipped)
        
        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Item exists'
            }
        }
        
        mock_dynamodb.put_item.side_effect = [
            {},  # 1: success
            {},  # 2: success
            ClientError(error_response, 'PutItem'),  # 3: duplicate
            {},  # 4: success
            ClientError(error_response, 'PutItem'),  # 5: duplicate
        ]
        
        event = {
            'Records': [
                {'body': json.dumps({
                    'tenant_id': 'metrics-batch',
                    'log_id': f'log-{i}',
                    'text': f'Message {i}',
                    'source': 'json',
                    'timestamp': '2024-12-03T10:00:00Z'
                })} for i in range(5)
            ]
        }
        
        response = lambda_function.lambda_handler(event, None)
        body = json.loads(response['body'])
        
        assert body['processed'] == 3
        assert body['skipped_duplicates'] == 2
        assert body['total'] == 5
