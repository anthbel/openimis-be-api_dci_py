"""
DCI Person Converter - SPDCI Compliant

Converts between SPDCI Person schema and OpenIMIS Individual model.
Follows SPDCI DO.IBR.01 Person Data Object specification.
"""
from datetime import datetime
from typing import Dict, Optional, Tuple


class PersonConverter:
    """
    Converter for SPDCI Person <-> OpenIMIS Individual

    Implements SPDCI Integrated Beneficiary Registry standard:
    https://standards.spdci.org/standards/wip-integrated-beneficiary-registry-v1.0.0/ibr/1.-crvs/data/data-objects/do.ibr.01-person
    """

    @staticmethod
    def individual_to_dci_person(individual):
        """
        Convert OpenIMIS Individual to SPDCI-compliant Person format.

        Args:
            individual: OpenIMIS Individual model instance

        Returns:
            dict: SPDCI Person object (DO.IBR.01)
        """
        person = {}

        # identifier (0...* DO.COM.01 Identifier)
        person["identifier"] = [
            {
                "type": "openimis_uuid",
                "value": str(individual.uuid),
                "system": "openimis"
            }
        ]

        # Add internal ID if available
        if hasattr(individual, 'id') and individual.id:
            person["identifier"].append({
                "type": "openimis_id",
                "value": str(individual.id),
                "system": "openimis"
            })

        # name (0...1 DO.COM.02 Name)
        if individual.first_name or individual.last_name:
            name_obj = {}
            if individual.first_name:
                name_obj["given_name"] = individual.first_name
            if individual.last_name:
                name_obj["family_name"] = individual.last_name

            # Construct full_name
            full_name_parts = []
            if individual.first_name:
                full_name_parts.append(individual.first_name)
            if individual.last_name:
                full_name_parts.append(individual.last_name)
            if full_name_parts:
                name_obj["full_name"] = " ".join(full_name_parts)

            person["name"] = name_obj

        # birth_date (0...1 DF.COM.STRING.01 date_time)
        # A date becomes an ISO 8601 date_time; a masked dob is the mask's string and is given as it is.
        if individual.dob:
            dob = individual.dob
            person["birth_date"] = f"{dob.isoformat()}T00:00:00Z" if hasattr(dob, "isoformat") else dob

        # sex (0...1 CD.COM.03 sex) - enumeration: male, female, others, unknown
        sex_value = PersonConverter._extract_sex(individual)
        if sex_value:
            person["sex"] = sex_value

        # phone_number (0...* DF.COM.STRING.02 phone_number)
        phone_numbers = PersonConverter._extract_phone_numbers(individual)
        if phone_numbers:
            person["phone_number"] = phone_numbers

        # email (0...* DF.COM.STRING.07 email)
        emails = PersonConverter._extract_emails(individual)
        if emails:
            person["email"] = emails

        # address (0...* DO.COM.03 Address)
        addresses = PersonConverter._extract_addresses(individual)
        if addresses:
            person["address"] = addresses

        # registration_date (0...1 DF.COM.STRING.01 date_time)
        if hasattr(individual, 'date_created') and individual.date_created:
            person["registration_date"] = individual.date_created.isoformat()

        # last_updated (0...1 DF.COM.STRING.01 date_time)
        if hasattr(individual, 'date_updated') and individual.date_updated:
            person["last_updated"] = individual.date_updated.isoformat()

        return person

    @staticmethod
    def _extract_sex(individual):
        """
        Extract sex from Individual model.

        Returns: "male", "female", "others", or "unknown" per SPDCI standard
        """
        # Try to get gender from direct attribute
        if hasattr(individual, 'gender') and individual.gender:
            try:
                if hasattr(individual.gender, 'code'):
                    code = individual.gender.code
                else:
                    code = individual.gender

                # Map to SPDCI sex enumeration
                sex_map = {
                    'M': 'male',
                    'F': 'female',
                    'O': 'others',
                    'U': 'unknown'
                }
                return sex_map.get(code, 'unknown')
            except AttributeError:
                pass

        # Try to get from json_ext
        if individual.json_ext and isinstance(individual.json_ext, dict):
            if 'sex' in individual.json_ext:
                return individual.json_ext['sex']
            if 'gender' in individual.json_ext:
                # Try to normalize it
                gender_value = str(individual.json_ext['gender']).lower()
                if gender_value in ['m', 'male']:
                    return 'male'
                elif gender_value in ['f', 'female']:
                    return 'female'
                elif gender_value in ['o', 'other', 'others']:
                    return 'others'
                else:
                    return 'unknown'

        return None

    @staticmethod
    def _extract_phone_numbers(individual):
        """
        Extract phone numbers from Individual model.

        Returns: list of phone numbers (E.164 format recommended)
        """
        phone_numbers = []

        # Try direct phone attribute
        if hasattr(individual, 'phone') and individual.phone:
            phone_numbers.append(str(individual.phone))

        # Try json_ext
        if individual.json_ext and isinstance(individual.json_ext, dict):
            if 'phone' in individual.json_ext:
                phone_numbers.append(str(individual.json_ext['phone']))
            if 'phone_number' in individual.json_ext:
                phone = individual.json_ext['phone_number']
                if isinstance(phone, list):
                    phone_numbers.extend([str(p) for p in phone])
                else:
                    phone_numbers.append(str(phone))

        # Remove duplicates while preserving order
        seen = set()
        unique_phones = []
        for phone in phone_numbers:
            if phone not in seen:
                seen.add(phone)
                unique_phones.append(phone)

        return unique_phones if unique_phones else None

    @staticmethod
    def _extract_emails(individual):
        """
        Extract email addresses from Individual model.

        Returns: list of email addresses
        """
        emails = []

        # Try direct email attribute
        if hasattr(individual, 'email') and individual.email:
            emails.append(str(individual.email))

        # Try json_ext
        if individual.json_ext and isinstance(individual.json_ext, dict):
            if 'email' in individual.json_ext:
                email = individual.json_ext['email']
                if isinstance(email, list):
                    emails.extend([str(e) for e in email])
                else:
                    emails.append(str(email))

        # Remove duplicates while preserving order
        seen = set()
        unique_emails = []
        for email in emails:
            if email not in seen:
                seen.add(email)
                unique_emails.append(email)

        return unique_emails if unique_emails else None

    @staticmethod
    def _extract_addresses(individual):
        """
        Extract addresses from Individual model.

        Returns: list of address objects (DO.COM.03 Address)
        """
        addresses = []

        # Try to get from location
        if hasattr(individual, 'location') and individual.location:
            try:
                location = individual.location
                address = {"type": "residential"}

                # Try to build address from location hierarchy
                if hasattr(location, 'name') and location.name:
                    address["city"] = location.name

                # Try to get parent locations (district, region, etc.)
                if hasattr(location, 'parent') and location.parent:
                    parent = location.parent
                    if hasattr(parent, 'name') and parent.name:
                        address["district"] = parent.name

                    # Try to get country from upper level
                    if hasattr(parent, 'parent') and parent.parent:
                        grandparent = parent.parent
                        if hasattr(grandparent, 'name') and grandparent.name:
                            address["region"] = grandparent.name

                # Add code if available
                if hasattr(location, 'code') and location.code:
                    address["location_code"] = location.code

                if len(address) > 1:  # More than just "type"
                    addresses.append(address)
            except Exception:
                pass

        # Try to get from json_ext
        if individual.json_ext and isinstance(individual.json_ext, dict):
            if 'address' in individual.json_ext:
                addr = individual.json_ext['address']
                if isinstance(addr, list):
                    addresses.extend(addr)
                elif isinstance(addr, dict):
                    addresses.append(addr)

        return addresses if addresses else None

    @staticmethod
    def dci_person_to_individual_data(dci_person):
        """
        Convert SPDCI Person to OpenIMIS Individual data.

        Args:
            dci_person: SPDCI Person dict (DO.IBR.01)

        Returns:
            dict: Data suitable for Individual model creation/update
        """
        data = {}

        # Extract name
        if "name" in dci_person and isinstance(dci_person["name"], dict):
            name = dci_person["name"]
            if "given_name" in name:
                data["first_name"] = name["given_name"]
            if "family_name" in name:
                data["last_name"] = name["family_name"]

        # Extract birth_date
        if "birth_date" in dci_person:
            try:
                # Parse ISO datetime and extract date
                dt = datetime.fromisoformat(dci_person["birth_date"].replace('Z', '+00:00'))
                data["dob"] = dt.date()
            except (ValueError, AttributeError):
                pass

        # Sex, phone and email are json_ext keys of the person (Individual has no such columns)
        json_ext = {}
        if "sex" in dci_person:
            json_ext["sex"] = dci_person["sex"]

        # Extract phone_number
        if "phone_number" in dci_person:
            phones = dci_person["phone_number"]
            if isinstance(phones, list) and phones:
                json_ext["phone"] = phones[0]  # Take first phone
                if len(phones) > 1:
                    json_ext["phone_numbers"] = phones
            elif phones:
                json_ext["phone"] = str(phones)

        # Extract email
        if "email" in dci_person:
            emails = dci_person["email"]
            if isinstance(emails, list) and emails:
                json_ext["email"] = emails[0]  # Take first email
                if len(emails) > 1:
                    json_ext["emails"] = emails
            elif emails:
                json_ext["email"] = str(emails)

        # Store address in json_ext (Individual model doesn't have address field)
        if "address" in dci_person:
            json_ext["address"] = dci_person["address"]

        # Store additional data in json_ext
        if json_ext:
            data["json_ext"] = json_ext

        return data

    @staticmethod
    def parse_person_id(person_id: str) -> Optional[str]:
        """
        Extract UUID from person_id format.

        Args:
            person_id: Person ID in format "namespace:resource:uuid"
                      Example: "openimis:individual:f6a0b402-0dc0-436e-a7bb-ec65dd4f011f"

        Returns:
            str: UUID or None if format invalid
        """
        if not person_id:
            return None

        parts = person_id.split(':')
        if len(parts) == 3 and parts[0] == 'openimis' and parts[1] == 'individual':
            return parts[2]

        return None

    @staticmethod
    def generate_person_id(individual) -> str:
        """
        Generate person_id from Individual.

        Args:
            individual: OpenIMIS Individual instance

        Returns:
            str: Person ID in format "openimis:individual:uuid"
        """
        return f"openimis:individual:{individual.uuid}"

    @staticmethod
    def create_individual_from_spdci(
        spdci_person: Dict,
        registry_type: str,
        user,
        metadata: Optional[Dict] = None,
        location=None,
    ) -> Tuple[object, Dict]:
        """
        Create Individual from SPDCI Person data, at ``location``.

        Args:
            spdci_person: SPDCI Person dict
            registry_type: "FR", "SR", or "IBR"
            user: Django User for audit trail
            metadata: Optional metadata (source_system, external_id)

        Returns:
            tuple: (Individual instance, json_ext dict)

        Raises:
            ValueError: If required data is missing
            ImportError: If Individual module not available
        """
        try:
            from individual.models import Individual
        except ImportError:
            raise ImportError("Individual module not available")

        # Import JsonExtManager here to avoid circular imports
        from ..utils.json_ext_manager import JsonExtManager

        # Extract base Individual data
        base_data = PersonConverter.dci_person_to_individual_data(spdci_person)

        # Structure json_ext with registry-specific data
        json_ext = JsonExtManager.structure_json_ext(
            registry_type=registry_type,
            spdci_person=spdci_person,
            metadata=metadata or {}
        )

        # The person's own json_ext keys (sex, phone, email, address) and the registry structure
        base_data['json_ext'] = {**base_data.get('json_ext', {}), **json_ext}
        base_data['location'] = location

        # Create Individual; the save records ``user`` as its creator and updater.
        individual = Individual(**base_data)
        individual.save(user=user)

        return individual, json_ext

    @staticmethod
    def update_individual_from_spdci(
        individual,
        spdci_person: Dict,
        registry_type: str,
        user,
        metadata: Optional[Dict] = None
    ) -> Tuple[object, Dict]:
        """
        Update Individual from SPDCI Person data.

        Args:
            individual: Existing Individual instance
            spdci_person: SPDCI Person dict (partial update allowed)
            registry_type: "FR", "SR", or "IBR"
            user: Django User for audit trail
            metadata: Optional metadata (source_system, external_id)

        Returns:
            tuple: (Updated Individual instance, updated json_ext dict)

        Raises:
            ValueError: If update fails
        """
        # Import JsonExtManager here to avoid circular imports
        from ..utils.json_ext_manager import JsonExtManager

        # Extract base Individual data (only fields provided)
        base_data = PersonConverter.dci_person_to_individual_data(spdci_person)

        # Update base fields if provided
        for field, value in base_data.items():
            if field == 'json_ext':
                continue  # Handle separately
            setattr(individual, field, value)

        # Update json_ext with the person's own keys, then with registry-specific data
        current_json_ext = {**(individual.json_ext or {}), **base_data.get('json_ext', {})}
        updated_json_ext = JsonExtManager.update_json_ext(
            current_json_ext=current_json_ext,
            spdci_person=spdci_person,
            registry_type=registry_type,
            metadata=metadata
        )

        individual.json_ext = updated_json_ext

        # Set audit fields
        individual.user_updated = user

        # Save (HistoryBusinessModel handles versioning)
        individual.save(user=user)

        return individual, updated_json_ext
