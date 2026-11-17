"""
Unit tests for PersonConverter

Tests the conversion between DCI Person format and OpenIMIS Individual model.
"""
from django.test import TestCase
from datetime import date, datetime
from unittest.mock import Mock
from api_dci.converters.person_converter import PersonConverter


class PersonConverterTestCase(TestCase):
    """
    Test cases for PersonConverter class.
    """

    def setUp(self):
        """
        Set up test data.
        """
        self.test_dci_person = {
            "@type": "Person",
            "identifier": [
                {
                    "type": "test_id",
                    "value": "test:person:123",
                    "system": "test"
                }
            ],
            "name": {
                "given_name": "John",
                "family_name": "Doe",
                "full_name": "John Doe"
            },
            "birth_date": "1990-01-15T00:00:00Z",
            "sex": "male",
            "phone_number": ["+1234567890"],
            "email": ["john.doe@example.com"]
        }

    def test_individual_to_dci_person_basic_fields(self):
        """
        Test conversion of Individual to DCI Person with basic fields.
        """
        # Create a mock Individual instance
        mock_individual = Mock()
        mock_individual.uuid = "f6a0b402-0dc0-436e-a7bb-ec65dd4f011f"
        mock_individual.id = 123
        mock_individual.first_name = "John"
        mock_individual.last_name = "Doe"
        mock_individual.dob = date(1990, 1, 15)
        mock_individual.phone = "+1234567890"
        mock_individual.email = "john.doe@example.com"
        mock_individual.json_ext = None
        mock_individual.location = None

        # Mock gender
        mock_gender = Mock()
        mock_gender.code = 'M'
        mock_individual.gender = mock_gender

        # Mock date_created and date_updated
        mock_individual.date_created = datetime(2024, 1, 1, 10, 0, 0)
        mock_individual.date_updated = datetime(2024, 1, 15, 14, 30, 0)

        # Convert to DCI Person
        result = PersonConverter.individual_to_dci_person(mock_individual)

        # Verify basic fields are correctly mapped
        self.assertIn('identifier', result)
        self.assertEqual(len(result['identifier']), 2)
        self.assertEqual(result['identifier'][0]['value'], "f6a0b402-0dc0-436e-a7bb-ec65dd4f011f")
        self.assertEqual(result['identifier'][0]['type'], "openimis_uuid")
        self.assertEqual(result['identifier'][1]['value'], "123")
        self.assertEqual(result['identifier'][1]['type'], "openimis_id")

        # Verify name
        self.assertIn('name', result)
        self.assertEqual(result['name']['given_name'], "John")
        self.assertEqual(result['name']['family_name'], "Doe")
        self.assertEqual(result['name']['full_name'], "John Doe")

        # Verify birth_date
        self.assertIn('birth_date', result)
        self.assertEqual(result['birth_date'], "1990-01-15T00:00:00Z")

        # Verify sex
        self.assertIn('sex', result)
        self.assertEqual(result['sex'], 'male')

        # Verify phone_number
        self.assertIn('phone_number', result)
        self.assertEqual(result['phone_number'], ["+1234567890"])

        # Verify email
        self.assertIn('email', result)
        self.assertEqual(result['email'], ["john.doe@example.com"])

        # Verify timestamps
        self.assertIn('registration_date', result)
        self.assertIn('last_updated', result)

    def test_individual_to_dci_person_gender_mapping(self):
        """
        Test gender mapping from Individual to DCI Person.
        """
        # Test M -> male
        mock_individual = Mock()
        mock_individual.uuid = "test-uuid"
        mock_individual.first_name = "Test"
        mock_individual.last_name = "User"
        mock_individual.dob = None
        mock_individual.phone = None
        mock_individual.email = None
        mock_individual.json_ext = None
        mock_individual.location = None

        mock_gender = Mock()
        mock_gender.code = 'M'
        mock_individual.gender = mock_gender

        result = PersonConverter.individual_to_dci_person(mock_individual)
        self.assertEqual(result['sex'], 'male')

        # Test F -> female
        mock_gender.code = 'F'
        result = PersonConverter.individual_to_dci_person(mock_individual)
        self.assertEqual(result['sex'], 'female')

        # Test O -> others
        mock_gender.code = 'O'
        result = PersonConverter.individual_to_dci_person(mock_individual)
        self.assertEqual(result['sex'], 'others')

        # Test U -> unknown
        mock_gender.code = 'U'
        result = PersonConverter.individual_to_dci_person(mock_individual)
        self.assertEqual(result['sex'], 'unknown')

        # Test unmapped code -> unknown
        mock_gender.code = 'X'
        result = PersonConverter.individual_to_dci_person(mock_individual)
        self.assertEqual(result['sex'], 'unknown')

    def test_individual_to_dci_person_missing_fields(self):
        """
        Test conversion when Individual has missing optional fields.
        """
        # Create minimal mock Individual with only required field (uuid)
        mock_individual = Mock()
        mock_individual.uuid = "minimal-uuid-123"
        mock_individual.id = None
        mock_individual.first_name = None
        mock_individual.last_name = None
        mock_individual.dob = None
        mock_individual.gender = None
        mock_individual.phone = None
        mock_individual.email = None
        mock_individual.json_ext = None
        mock_individual.location = None
        mock_individual.date_created = None
        mock_individual.date_updated = None

        # Convert to DCI Person
        result = PersonConverter.individual_to_dci_person(mock_individual)

        # Verify identifier is always present (required field)
        self.assertIn('identifier', result)
        self.assertEqual(len(result['identifier']), 1)
        self.assertEqual(result['identifier'][0]['value'], "minimal-uuid-123")

        # Verify optional fields are not present when None
        self.assertNotIn('name', result)
        self.assertNotIn('birth_date', result)
        self.assertNotIn('sex', result)
        self.assertNotIn('phone_number', result)
        self.assertNotIn('email', result)
        self.assertNotIn('address', result)
        self.assertNotIn('registration_date', result)
        self.assertNotIn('last_updated', result)

        # Test with partial data (only first name, no last name)
        mock_individual.first_name = "John"
        result = PersonConverter.individual_to_dci_person(mock_individual)

        self.assertIn('name', result)
        self.assertEqual(result['name']['given_name'], "John")
        self.assertNotIn('family_name', result['name'])
        self.assertEqual(result['name']['full_name'], "John")

    def test_dci_person_to_individual_data_basic_fields(self):
        """
        Test conversion of DCI Person to Individual data with basic fields.
        """
        result = PersonConverter.dci_person_to_individual_data(self.test_dci_person)

        # Verify basic field mappings
        self.assertEqual(result['first_name'], "John")
        self.assertEqual(result['last_name'], "Doe")
        self.assertEqual(result['dob'], date(1990, 1, 15))
        # Sex, phone and email are json_ext keys, not Individual fields
        self.assertNotIn('gender_code', result)
        self.assertNotIn('phone', result)
        self.assertNotIn('email', result)
        self.assertEqual(result['json_ext']['sex'], 'male')
        self.assertEqual(result['json_ext']['phone'], "+1234567890")
        self.assertEqual(result['json_ext']['email'], "john.doe@example.com")

    def test_dci_person_to_individual_data_sex_in_json_ext(self):
        """
        Test that the DCI sex is kept as given in json_ext.
        """
        for sex in ("male", "female", "others", "unknown"):
            result = PersonConverter.dci_person_to_individual_data({"sex": sex})
            self.assertEqual(result, {"json_ext": {"sex": sex}})

    def test_dci_person_to_individual_data_missing_fields(self):
        """
        Test conversion when DCI Person has missing optional fields.
        """
        minimal_person = {
            "@type": "Person",
            "id": "test:person:456"
        }

        result = PersonConverter.dci_person_to_individual_data(minimal_person)

        # Should return empty dict or dict with only present fields
        self.assertIsInstance(result, dict)
        self.assertNotIn('first_name', result)
        self.assertNotIn('last_name', result)

    def test_dci_person_to_individual_data_date_parsing(self):
        """
        Test date parsing in conversion.
        """
        person = {
            "birth_date": "1990-01-15T00:00:00Z"
        }

        result = PersonConverter.dci_person_to_individual_data(person)

        # Verify date is correctly parsed
        self.assertEqual(result['dob'], date(1990, 1, 15))
        self.assertIsInstance(result['dob'], date)
