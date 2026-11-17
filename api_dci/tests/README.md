# DCI API Tests

Unit and integration tests for the `openimis-be-api_dci_py` module.

## Test Structure

```
api_dci/tests/
├── __init__.py
├── README.md                       # This file
├── utils.py                        # Test utilities
├── mixin/                          # Reusable mixins
│   ├── dciApiTestMixin.py         # DCI API Mixin
│   ├── genericTestMixin.py        # Generic mixin
│   ├── logInMixin.py              # Authentication mixin
│   └── personTestMixin.py         # Person data mixin
├── test_sync_search.py            # Sync search endpoint tests
├── test_subscription.py           # Subscribe/unsubscribe tests
├── test_person_detail.py          # GET person/{id} tests
├── test_personConverter.py        # PersonConverter tests
└── test_personSerializer.py       # Serializer tests
```

## Tested Endpoints

### ✅ Implemented

1. **Sync Search** (`test_sync_search.py`)
   - `POST /api/api_dci/registry/sync/search`
   - Query types: sync, predicate, expression, idtype-value
   - Tests: format validation, search by fields, no results, FR response structure

2. **Subscription** (`test_subscription.py`)
   - `POST /api/api_dci/registry/subscribe`
   - `POST /api/api_dci/registry/unsubscribe`
   - Tests: valid subscription, event types, filters, expiry, unsubscribe

3. **Person Detail** (`test_person_detail.py`)
   - `GET /api/api_dci/registry/person/{person_id}`
   - Tests: valid ID, invalid format, SPDCI Person structure

## Prerequisites

### Required OpenIMIS Modules

- `individual` - CoreMIS Individual Module
- `core` - OpenIMIS core module (User, Gender)
- `location` - Location module (optional)

### Configuration

Tests require a Django test database. Automatic configuration via `settings.py`.

## Running the Tests

### All tests

```bash
# From the openIMIS project root
python manage.py test api_dci.tests

# Or with coverage
coverage run --source='api_dci' manage.py test api_dci.tests
coverage report
```

### Specific tests

```bash
# Sync search only
python manage.py test api_dci.tests.test_sync_search

# Subscription only
python manage.py test api_dci.tests.test_subscription

# Person detail only
python manage.py test api_dci.tests.test_person_detail

# A specific test class
python manage.py test api_dci.tests.test_sync_search.SyncSearchAPITestCase

# A specific test
python manage.py test api_dci.tests.test_sync_search.SyncSearchAPITestCase.test_sync_search_valid_request
```

### With verbosity

```bash
# Level 2: displays each test
python manage.py test api_dci.tests --verbosity=2

# Level 3: very detailed
python manage.py test api_dci.tests --verbosity=3
```

### With keepdb (faster)

```bash
# Keeps the test DB between executions
python manage.py test api_dci.tests --keepdb
```

## GitHub Actions

Tests are automatically executed in GitHub Actions during pull requests.

### Configuration

The CI workflow is defined in `.github/workflows/ci.yml` and uses OpenIMIS's reusable workflow.

### Manual workflow

You can manually trigger tests via GitHub Actions:
1. Go to the "Actions" tab of the repository
2. Select "Module CI"
3. Click "Run workflow"

## Test Structure

### Simple example

```python
from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status
from api_dci.tests.mixin import DCIApiTestMixin, LogInMixin

class MyAPITestCase(DCIApiTestMixin, LogInMixin, APITestCase):
    base_url = "/api/api_dci/registry/endpoint"

    def setUp(self):
        super().setUp()
        self.test_user = self.get_or_create_user_api()
        self.login()

    def test_my_endpoint(self):
        request_data = {...}

        response = self.client.post(
            self.base_url,
            data=request_data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertIn('header', response_data)
```

### Available mixins

- `DCIApiTestMixin`: Helper methods for DCI API
- `LogInMixin`: Automatic authentication
- `PersonTestMixin`: Test Person data creation
- `GenericTestMixin`: Generic utilities

## Test Data

### Individual

Tests automatically create test `Individual` objects:

```python
Individual.objects.create(
    first_name="Test",
    last_name="Person",
    dob="1990-01-15",
    gender=male_gender,
    user_created=test_user,
    user_updated=test_user
)
```

### Cleanup

Tests automatically clean up created data in `tearDown()`.

## Coverage

### Generate a coverage report

```bash
# Run tests with coverage
coverage run --source='api_dci' manage.py test api_dci.tests

# Display the console report
coverage report

# Generate an HTML report
coverage html

# Open the report
open htmlcov/index.html
```

### Coverage target

- **Expected minimum:** 80%
- **Goal:** 90%+

## Debugging

### Display logs during tests

```bash
# Django logs
python manage.py test api_dci.tests --debug-mode

# With pdb (breakpoint)
import pdb; pdb.set_trace()
```

### Test with a real database

```bash
# Use the development DB (caution!)
python manage.py test api_dci.tests --keepdb --settings=openimis.settings_dev
```

## SPDCI Compliance Tests

Django unit tests are complementary to SPDCI compliance tests:

**Django tests (this module):**
- Python/Django unit tests
- Verify business logic
- Test integration with Individual/Core

**SPDCI Compliance tests:**
```bash
cd /path/to/spdci-compliance
DOMAIN=fr API_BASE_URL=http://localhost:8000/api/api_dci npm run test:fr
```

## Contributing

### Add a new test

1. Create a `test_*.py` file in `api_dci/tests/`
2. Inherit from `APITestCase` and appropriate mixins
3. Implement `setUp()` and `tearDown()`
4. Write tests following conventions

### Conventions

- Name tests: `test_<feature>_<scenario>`
- Explicit docstrings for each test
- Clear assertions with messages
- Data cleanup in `tearDown()`

### PR Checklist

- [ ] All tests pass locally
- [ ] Coverage maintained/improved
- [ ] Tests for each new endpoint/feature
- [ ] Documentation updated if necessary

## Troubleshooting

### "Individual module not available"

**Cause:** The `individual` module is not installed.

**Solution:**
```bash
# Install the individual module
pip install openimis-be-individual

# Or check INSTALLED_APPS
# settings.py must contain: 'individual'
```

### "Permission denied"

**Cause:** The test user doesn't hold the DCI person rights, or the person is outside the
user's locations.

**Solution:**
```python
# The user of LogInMixin.get_or_create_user_api() holds the DCI person rights through a role.
# Any other user needs a role carrying them, and districts covering the persons it reads:
from core.test_helpers import create_right_only_user
user = create_right_only_user("reader", ["gql_dci_person_search_perms"], ["R1D1"])
```

### Slow tests

**Possible causes:**
- Too much data created
- No `--keepdb`
- Migrations too long

**Solutions:**
```bash
# Use keepdb
python manage.py test api_dci.tests --keepdb

# Parallelize (if supported)
python manage.py test api_dci.tests --parallel=4
```

## Resources

- [Django Testing](https://docs.djangoproject.com/en/stable/topics/testing/)
- [DRF Testing](https://www.django-rest-framework.org/api-guide/testing/)
- [SPDCI FR Spec](https://api.spdci.org/release/html/fr_api_v1.0.0.html)
- [OpenIMIS Developer Guide](https://docs.openimis.org/developer_manual/)
