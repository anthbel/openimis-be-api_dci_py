# OpenIMIS Backend DCI API Module

## Overview

This module provides a REST API for OpenIMIS following the **DCI (Digital Convergence Initiative)** standard for social protection data exchange. It enables interoperability between OpenIMIS and other systems that implement the DCI specification.

## DCI Standard

The DCI (Digital Convergence Initiative) is a standard for sharing information in the context of social protection. It defines:

- Data object schemas (Person, Household, etc.)
- API endpoints for synchronization
- Message formats for request/response

### References

- DCI Registry Core API: https://api.spdci.org/release/html/registry_core_api_v1.0.0.html
- DCI Person Schema: https://schema.spdci.org/core/v1/data/Person.jsonld

## Features

### Supported SPDCI Standards

This module implements the following SPDCI (Social Protection Digital Convergence Initiative) registry standards:

- ✅ **FR (Farmer Registry)** - SPDCI FR v1.0.0 compliant (**Tested & Certified**)
- ✅ **IBR (Integrated Beneficiary Registry)** - Person registry following SPDCI DO.IBR.01 specification
- 🔄 **SR (Social Registry)** - Social registry format (Ready, pending testing)

### Implemented Endpoints

#### Person Registry (IBR)
- `POST /api/api_dci/registry/sync/search` - Synchronous search for Person records
- `GET /api/api_dci/registry/person/{id}` - Retrieve a single Person by ID
- `POST /api/api_dci/login/` - JWT authentication endpoint

#### Planned Endpoints
- `POST /api/api_dci/registry/search` - Asynchronous search (FR standard)
- `POST /api/api_dci/registry/subscribe` - Event subscription
- `POST /api/api_dci/registry/notify` - Event notifications
- `POST /api/dci/reg/sync/search` - Synchronous search for Person records following DCI standard
- `POST /api/dci/reg/search` - Asynchronous search for Person records following DCI standard

## Installation

### Requirements

- Python 3.6+
- Django 2.1+
- Django REST Framework
- drf-spectacular (for OpenAPI documentation)
- openimis-be-core
- openimis-be-individual

### Setup

1. Install the module:
```bash
pip install openimis-be-api_dci
```

2. Add to your OpenIMIS configuration (`openimis.json`):
```json
{
  "modules": [
    {
      "name": "api_dci",
      "pip": "openimis-be-api_dci"
    }
  ]
}
```

3. **Configure the SPDCI registry type** (choose one):
```bash
# Option 1: Environment variable (recommended)
export SPDCI_REGISTRY_TYPE=fr  # or 'sr', 'ibr'

# Option 2: Add to your .env file
SPDCI_REGISTRY_TYPE=fr

# Option 3: Docker environment in docker-compose.yml
environment:
  - SPDCI_REGISTRY_TYPE=fr
```

**Available registry types:**
- `fr` - Farmer Registry (SPDCI FR v1.0.0) - **Default**
- `sr` - Social Registry (SPDCI SR v1.0.0)
- `ibr` - Integrated Beneficiary Registry (SPDCI IBR v1.0.0)

**Important:** One OpenIMIS instance = one registry type. To test different registry types, change the `SPDCI_REGISTRY_TYPE` environment variable and restart the backend.

4. The module will be automatically loaded by OpenIMIS.

## API Documentation

Once installed, access the API documentation at:

- OpenAPI Schema: `/api/dci/docs/`
- Swagger UI: `/api/dci/docs/swagger/`
- ReDoc: `/api/dci/docs/redoc/`

## SPDCI Compliance

### Person Data Object (DO.IBR.01)

The module implements strict SPDCI DO.IBR.01 Person Data Object specification:

**Specification**: [SPDCI DO.IBR.01 Person](https://standards.spdci.org/standards/wip-integrated-beneficiary-registry-v1.0.0/ibr/1.-crvs/data/data-objects/do.ibr.01-person)

| SPDCI Field | Type | OpenIMIS Field | Notes |
|------------|------|----------------|-------|
| `identifier` | array | `uuid`, `id` | Array of {type, value, system} objects |
| `name` | object | `first_name`, `last_name` | Object with given_name, family_name, full_name |
| `birth_date` | datetime | `dob` | ISO 8601 datetime format (YYYY-MM-DDTHH:MM:SSZ) |
| `sex` | enum | `gender.code` | Values: "male", "female", "others", "unknown" |
| `phone_number` | array | `phone`, `json_ext` | Array of phone numbers (E.164 recommended) |
| `email` | array | `email`, `json_ext` | Array of email addresses |
| `address` | array | `location`, `json_ext` | Array of address objects (DO.COM.03) |
| `registration_date` | datetime | `date_created` | ISO 8601 format |
| `last_updated` | datetime | `date_updated` | ISO 8601 format |

### Multi-Standard Architecture

The module supports multiple SPDCI registry standards through a flexible endpoint structure:

```
/api/api_dci/
├── registry/
│   ├── sync/search     # IBR synchronous search (simple queries)
│   ├── search          # FR asynchronous search (complex queries with expressions)
│   ├── person/{id}     # IBR direct person lookup
│   ├── subscribe       # FR event subscriptions
│   └── notify          # FR event notifications
└── login/              # JWT authentication
```

#### Query Type Support

The API supports multiple SPDCI query types through a unified endpoint:

**✅ SPDCI FR Standard** (Fully Compliant):
- `idtype-value` - Search by identifier (FARMER_ID, UIN, NIN, etc.)
- `expression` - Free-form expression queries (implementation-specific)
- `predicate` - Structured predicate queries with operators (eq, gt, lt, ge, le, in)

**✅ Legacy OpenIMIS**:
- `sync` - Backward compatible synchronous queries
- `async` - Backward compatible asynchronous queries

**Query Examples:**

1. **ID Type-Value Query (SPDCI FR)**
```json
{
  "query_type": "idtype-value",
  "query": {
    "type": "FARMER_ID",
    "value": "FARMER-TEST-001"
  }
}
```

2. **Predicate Query (SPDCI FR)**
```json
{
  "query_type": "predicate",
  "query": [{
    "seq_num": 1,
    "expression1": {
      "attribute_name": "first_name",
      "operator": "eq",
      "attribute_value": "John"
    }
  }]
}
```

3. **Expression Query (SPDCI FR)**
```json
{
  "query_type": "expression",
  "query": {
    "type": "ns:org:QueryType:expression",
    "value": {
      "expression": {
        "firstName": "John",
        "lastName": "Doe"
      }
    }
  }
}
```

All query types share the same Person data object schema for consistency.

## Example Usage

### Search Request

```bash
POST /api/dci/reg/sync/search
Content-Type: application/json
Authorization: Bearer <token>

{
  "signature": "Signature: namespace=\"spdci\", kidId=\"...\", algorithm=\"ed25519\", headers=\"...\", signature=\"...\"",
  "header": {
    "version": "1.0.0",
    "message_id": "uuid-1234",
    "message_ts": "2026-02-20T23:54:00Z",
    "action": "search",
    "sender_id": "external-system",
    "receiver_id": "openimis",
    "sender_uri": "https://callback.example.com",
    "is_msg_encrypted": false
  },
  "message": {
    "transaction_id": "tx-5678",
    "search_request": [
      {
        "reference_id": "ref-111",
        "timestamp": "2026-02-20T23:54:00Z",
        "search_criteria": {
          "reg_type": "person",
          "query_type": "sync",
          "query": {
            "type": "ns:org:QueryType:expression",
            "value": {
              "expression": {
                "query": {
                  "$and": [
                    {"firstName": {"$eq": "John"}},
                    {"lastName": {"$eq": "Doe"}}
                  ]
                }
              }
            }
          }
        }
      }
    ]
  }
}
```

### Search Response (SPDCI DO.IBR.01 Format)

```json
{
  "signature": "Signature: namespace=\"spdci\", kidId=\"...\", algorithm=\"ed25519\", headers=\"...\", signature=\"...\"",
  "header": {
    "version": "1.0.0",
    "message_id": "response-uuid-1234",
    "message_ts": "2026-02-20T23:54:01.123456Z",
    "action": "on-search",
    "status": "success",
    "sender_id": "openimis",
    "receiver_id": "external-system",
    "sender_uri": "https://callback.example.com",
    "total_count": 1,
    "is_msg_encrypted": false,
    "meta": {}
  },
  "message": {
    "transaction_id": "tx-5678",
    "search_response": [
      {
        "reference_id": "ref-111",
        "timestamp": "2026-02-20T23:54:01Z",
        "status": "succ",
        "status_reason_code": "succ",
        "status_reason_message": "Success",
        "registry_data": {
          "data": [
            {
              "@type": "Person",
              "id": "openimis:individual:12345",
              "firstName": "John",
              "lastName": "Doe",
              "dob": "1990-01-15",
              "gender": "Male"
            }
          ]
        }
      }
    ]
  }
}
```

## Permissions

The module uses OpenIMIS Individual permissions:

- **Search**: `individual.gql_query_individuals_perms`
- **Create**: `individual.gql_mutation_create_individuals_perms`
- **Update**: `individual.gql_mutation_update_individuals_perms`
- **Delete**: `individual.gql_mutation_delete_individuals_perms`

Sync and async search of a record provider type require the provider's right instead
(see Record Providers).

## Record Providers

Other modules can serve record types other than Person through `sync/search` and
`async/search`. A provider implements:

- `required_right() -> str`: the right a caller must hold to search this type;
- `search(user, query, cursor, limit) -> (records, next_cursor)`: one page, with
  `next_cursor` an opaque string, or `None` after the last page.

Register one with the `record_providers` setting of the `api_dci` module configuration
(`{reg_record_type: dotted path}`) or with
`api_dci.record_providers.register_record_provider(reg_record_type, dotted_path)`.
Paths are resolved when registered; a path that does not resolve stops startup.
`Person`, `Farmer` and `Member` cannot be registered: they stay with the Person search.

Search picks its permission from `search_criteria.reg_record_type` before any other check:

| `reg_record_type` | Search | Permission |
| --- | --- | --- |
| registered provider type | the provider | the provider's `required_right()` |
| absent, or any type not registered | Person (Individual) | `individual.gql_query_individuals_perms` |
| a provider type mixed with another type in one message | none | refused (403) |

A provider page is requested and returned in the SPDCI envelope:

- page size: `search_criteria.pagination.page_size` (1 to 100, default 100);
- cursor: `header.meta.cursor` on the request, `header.meta.next_cursor` on the response
  (`null` after the last page);
- records: `search_response[].data.reg_records`, with `data.reg_record_type`.

The SPDCI `pagination` objects hold only integer `page_size`, `page_number` and
`total_count`, so the cursor travels in `header.meta`, which the standard leaves to the
implementation.

`api_dci.client.DCISearchClient(base_url, credential_env)` pulls these pages from another
registry. It accepts only `https://` URLs, sends the bearer token held in the environment
key `credential_env`, and raises `DCIClientConfigurationError`, `DCITransportError`,
`DCIHTTPError` or `DCIResponseError`.

Receivers pull provider pages on their own schedule; nothing is pushed to them. Person
records are pushed to subscribers only when `DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL=True`, and
it defaults to `False`.

## Module Structure

```
openimis-be-api_dci_py/
├── api_dci/
│   ├── __init__.py
│   ├── apps.py              # Django app configuration
│   ├── urls.py              # URL routing
│   ├── permissions.py       # Permission classes
│   ├── record_providers.py  # Record provider registry and search dispatch
│   ├── client.py            # Outbound DCI search client
│   ├── serializers/         # DCI serializers
│   │   ├── __init__.py
│   │   └── person_serializer.py
│   ├── views/               # API views
│   │   ├── __init__.py
│   │   └── person_viewset.py
│   ├── converters/          # Data converters
│   │   ├── __init__.py
│   │   └── person_converter.py
│   └── tests/               # Unit tests
│       └── __init__.py
├── setup.py
└── README.md
```

## Development

### Running Tests

```bash
python -m pytest api_dci/tests/
```

### SPDCI Compliance Testing

This module has been **tested and certified** for SPDCI Farmer Registry (FR) v1.0.0 compliance using the official SPDCI compliance test suite.

#### ✅ Certified Compliance

**SPDCI FR v1.0.0** - Sync Search Endpoint
- **Test ID**: `FR-CORE-RG-SYNC-SEARCH-01`
- **Status**: ✅ PASSED (7/7 steps)
- **Validated**: March 2026
- **Test Suite**: OpenSPP SPDCI Compliance Tests

**Compliance Areas:**
- ✅ Message envelope structure (signature, header, message)
- ✅ Query type support (idtype-value, expression, predicate)
- ✅ Response format (correlation_id, search_response, data)
- ✅ Schema validation against OpenAPI FR spec
- ✅ Performance requirements (< 15s response time)
- ✅ Security (JWT Bearer token authentication)
- ✅ Error handling (400/401/500 responses)

#### Setup Compliance Tests

**Documentation**: [Testing Your Registry](https://github.com/OpenSPP/spdci-compliance/blob/fix/align-configs-with-spec/docs/testing-your-registry.md)

1. Clone the SPDCI compliance repository:
```bash
git clone https://github.com/OpenSPP/spdci-compliance.git
cd spdci-compliance
npm install
```

2. **Configure OpenIMIS for the registry type you want to test:**
```bash
# For FR (Farmer Registry) tests
export SPDCI_REGISTRY_TYPE=fr
docker restart openimis-dist_dkr-backend-1

# For SR (Social Registry) tests
export SPDCI_REGISTRY_TYPE=sr
docker restart openimis-dist_dkr-backend-1

# For IBR (Beneficiary Registry) tests
export SPDCI_REGISTRY_TYPE=ibr
docker restart openimis-dist_dkr-backend-1
```

3. Get authentication token:
```bash
TOKEN=$(curl -s -X POST http://localhost/api/api_dci/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"Admin","password":"admin123"}' \
  | jq -r '.token')
```

4. Run compliance tests:
```bash
# Test FR (Farmer Registry) compliance
export DCI_AUTH_TOKEN="$TOKEN"
export API_BASE_URL=http://localhost/api/api_dci/
npm run test:fr

# Test specific FR sync/search endpoint
npm run test:fr -- --tags '@req=FR-CORE-RG-SYNC-SEARCH-01'

# Run all FR smoke tests
npm run test:fr -- --tags '@smoke'

# Test SR (Social Registry) - after configuring SPDCI_REGISTRY_TYPE=sr
npm run test:social

# Test IBR (Beneficiary Registry) - after configuring SPDCI_REGISTRY_TYPE=ibr
npm run test:ibr
```

**Note:** Each registry type must be tested separately. Change `SPDCI_REGISTRY_TYPE` and restart the backend between test suites.

#### Docker-based Testing

If using the OpenIMIS Docker setup with the SPDCI compliance container:

```bash
# Get fresh token
TOKEN=$(docker exec openimis-dist_dkr-backend-1 python -c "
import requests, json
resp = requests.post('http://localhost:8000/api/api_dci/login/',
                      json={'username': 'Admin', 'password': 'admin123'})
print(json.loads(resp.text)['token'])
")

# Run FR compliance tests
docker exec -e DCI_AUTH_TOKEN="$TOKEN" \
  openimis-dist_dkr-spdci-compliance-1 \
  npm run test:fr -- --tags '@req=FR-CORE-RG-SYNC-SEARCH-01'
```

**Expected Output:**
```
1 scenario (1 passed)
7 steps (7 passed)
0m00.289s
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run compliance tests to ensure SPDCI standard conformity
5. Submit a pull request

## License

GNU Affero General Public License v3.0

## Links

- [OpenIMIS Website](https://openimis.org/)
- [DCI Specification](https://api.spdci.org/)
- [OpenIMIS GitHub](https://github.com/openimis)