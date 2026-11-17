"""
Test Utilities for DCI tests

Common utility functions for loading test data and preparing test requests.
"""
import json
from pathlib import Path
from unittest import mock

from api_dci.apps import ApiDciConfig

# The host of the sender_uri the async search tests call back.
CALLBACK_HOST = "mis.example.org"


def allow_callback_host(test_case, host=CALLBACK_HOST):
    """Lists ``host`` in ``callback_allowed_hosts`` for the duration of ``test_case``."""
    patcher = mock.patch.object(ApiDciConfig, "callback_allowed_hosts", [host])
    patcher.start()
    test_case.addCleanup(patcher.stop)


def get_test_data_path():
    """
    Get the path to the test data directory.

    Returns:
        Path: Path to test/data directory
    """
    current_dir = Path(__file__).parent
    return current_dir / 'test'


def load_json_from_test_data(file_name):
    """
    Load JSON data from test data directory.

    Args:
        file_name: Name of JSON file (e.g., 'test_person.json')

    Returns:
        dict: Loaded JSON data
    """
    file_path = get_test_data_path() / file_name
    with open(file_path, 'r') as f:
        return json.load(f)


def load_and_replace_json(file_path, replacements=None):
    """
    Load JSON and replace placeholders with actual values.

    Args:
        file_path: Path to JSON file (can be relative to test dir)
        replacements: Dict of {placeholder: actual_value}

    Returns:
        dict: JSON data with replacements applied
    """
    # Handle relative paths
    if not file_path.startswith('/'):
        file_path = str(get_test_data_path() / file_path.lstrip('/'))

    with open(file_path, 'r') as f:
        content = f.read()

    # Apply replacements
    if replacements:
        for placeholder, value in replacements.items():
            content = content.replace(str(placeholder), str(value))

    return json.loads(content)


def get_connection_payload(username="admin", password="admin"):
    """
    Get a basic connection payload for authentication.

    Args:
        username: Username for login
        password: Password for login

    Returns:
        dict: Login payload
    """
    return {
        "username": username,
        "password": password
    }


def create_dci_search_request(query_data, transaction_id="test-txn", sender_id="test-sender"):
    """
    Create a DCI search request message.

    Args:
        query_data: Query person data (dict)
        transaction_id: Transaction ID
        sender_id: Sender ID

    Returns:
        dict: DCI search request
    """
    from datetime import datetime

    return {
        "signature": {},
        "header": {
            "version": "1.0.0",
            "message_id": f"{transaction_id}-msg",
            "message_ts": datetime.utcnow().isoformat() + 'Z',
            "action": "search",
            "sender_id": sender_id,
            "receiver_id": "openimis"
        },
        "message": {
            "transaction_id": transaction_id,
            "search_criteria": {
                "reg_type": "person",
                "query_type": "sync",
                "query": query_data
            }
        }
    }


def assert_dci_message_structure(test_case, response_data):
    """
    Assert that response has valid DCI message structure.

    Args:
        test_case: TestCase instance
        response_data: DCI message dict
    """
    # Check top-level structure
    test_case.assertIn('header', response_data)
    test_case.assertIn('message', response_data)

    # Check header fields
    header = response_data['header']
    test_case.assertIn('version', header)
    test_case.assertIn('message_id', header)
    test_case.assertIn('message_ts', header)
    test_case.assertIn('action', header)
    test_case.assertIn('sender_id', header)
