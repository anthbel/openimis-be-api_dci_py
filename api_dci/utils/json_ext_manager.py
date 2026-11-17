"""
JSON Ext Manager

Utility for managing Individual.json_ext structure for SPDCI registry types.
Handles FR (Farmer Registry), SR (Social Registry), and IBR (Integrated Beneficiary Registry).
"""
from typing import Dict, Optional
from datetime import datetime


class JsonExtManager:
    """Manages json_ext structure for SPDCI registry types."""

    @staticmethod
    def structure_json_ext(
        registry_type: str,
        spdci_person: Dict,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Structure json_ext data according to registry type.

        Args:
            registry_type: "FR", "SR", or "IBR"
            spdci_person: SPDCI Person data dict
            metadata: Optional metadata (source_system, external_id, etc.)

        Returns:
            dict: Structured json_ext data
        """
        if not metadata:
            metadata = {}

        base_structure = {
            "registry_type": registry_type,
            "registry_metadata": {
                "source_system": metadata.get("source_system", ""),
                "external_id": metadata.get("external_id", ""),
                "last_sync": datetime.utcnow().isoformat() + 'Z'
            },
            "spdci_data": {}
        }

        # The registry type compares in upper case: config.get_registry_type gives it in lower case.
        kind = str(registry_type or "").upper()
        if kind == "FR":
            base_structure["spdci_data"] = JsonExtManager._extract_fr_data(spdci_person)
        elif kind == "SR":
            base_structure["spdci_data"] = JsonExtManager._extract_sr_data(spdci_person)
        elif kind == "IBR":
            base_structure["spdci_data"] = JsonExtManager._extract_ibr_data(spdci_person)
        else:
            # Fallback: store raw data
            base_structure["spdci_data"] = spdci_person

        return base_structure

    @staticmethod
    def extract_registry_data(registry_type: str, spdci_person: Dict) -> Optional[Dict]:
        """The registry-specific data of ``spdci_person`` that ``spdci_data`` keeps for ``registry_type`` (any
        case); None for another registry type."""
        extract = {
            "FR": JsonExtManager._extract_fr_data,
            "SR": JsonExtManager._extract_sr_data,
            "IBR": JsonExtManager._extract_ibr_data,
        }.get(str(registry_type or "").upper())
        return extract(spdci_person) if extract else None

    @staticmethod
    def _extract_fr_data(spdci_person: Dict) -> Dict:
        """
        Extract Farmer Registry specific data.
        Based on SPDCI DO.FR.04 Farmer specification.

        Args:
            spdci_person: SPDCI Person/Farmer data

        Returns:
            dict: FR-specific data for json_ext
        """
        fr_data = {}

        # DO.FR.02 Member - farmer_personal_details
        if "farmer_personal_details" in spdci_person:
            fr_data["farmer_personal_details"] = spdci_person["farmer_personal_details"]

        # DO.FR.03 Group - family_details
        if "family_details" in spdci_person:
            fr_data["family_details"] = spdci_person["family_details"]

        # DO.FR.05 FarmInfo - farm_details (array)
        if "farm_details" in spdci_person:
            fr_data["farm_details"] = spdci_person["farm_details"]

        # DO.FR.09 Machinery - machineries_details (array)
        if "machineries_details" in spdci_person:
            fr_data["machineries_details"] = spdci_person["machineries_details"]

        # Additional attributes
        if "additional_attributes" in spdci_person:
            fr_data["additional_attributes"] = spdci_person["additional_attributes"]

        # Timestamps
        if "registration_date" in spdci_person:
            fr_data["registration_date"] = spdci_person["registration_date"]
        if "last_updated" in spdci_person:
            fr_data["last_updated"] = spdci_person["last_updated"]

        return fr_data

    @staticmethod
    def _extract_sr_data(spdci_person: Dict) -> Dict:
        """
        Extract Social Registry specific data.
        Based on SPDCI DO.SR.01 Person specification.

        Args:
            spdci_person: SPDCI Person data

        Returns:
            dict: SR-specific data for json_ext
        """
        sr_data = {}

        # Household details
        if "household_details" in spdci_person:
            sr_data["household_details"] = spdci_person["household_details"]

        # Socio-economic details
        if "socio_economic_details" in spdci_person:
            sr_data["socio_economic_details"] = spdci_person["socio_economic_details"]

        # Vulnerability details
        if "vulnerability_details" in spdci_person:
            sr_data["vulnerability_details"] = spdci_person["vulnerability_details"]

        # Program enrollment
        if "program_enrollment" in spdci_person:
            sr_data["program_enrollment"] = spdci_person["program_enrollment"]

        # Additional attributes
        if "additional_attributes" in spdci_person:
            sr_data["additional_attributes"] = spdci_person["additional_attributes"]

        # Timestamps
        if "registration_date" in spdci_person:
            sr_data["registration_date"] = spdci_person["registration_date"]
        if "last_updated" in spdci_person:
            sr_data["last_updated"] = spdci_person["last_updated"]

        return sr_data

    @staticmethod
    def _extract_ibr_data(spdci_person: Dict) -> Dict:
        """
        Extract Integrated Beneficiary Registry specific data.
        Based on SPDCI DO.IBR.01 Person specification.

        Args:
            spdci_person: SPDCI Person data

        Returns:
            dict: IBR-specific data for json_ext
        """
        ibr_data = {}

        # Beneficiary details
        if "beneficiary_details" in spdci_person:
            ibr_data["beneficiary_details"] = spdci_person["beneficiary_details"]

        # Program participation
        if "program_participation" in spdci_person:
            ibr_data["program_participation"] = spdci_person["program_participation"]

        # Verification details
        if "verification_details" in spdci_person:
            ibr_data["verification_details"] = spdci_person["verification_details"]

        # Additional attributes
        if "additional_attributes" in spdci_person:
            ibr_data["additional_attributes"] = spdci_person["additional_attributes"]

        # Timestamps
        if "registration_date" in spdci_person:
            ibr_data["registration_date"] = spdci_person["registration_date"]
        if "last_updated" in spdci_person:
            ibr_data["last_updated"] = spdci_person["last_updated"]

        return ibr_data

    @staticmethod
    def get_spdci_data(json_ext: Dict, registry_type: Optional[str] = None) -> Dict:
        """
        Get SPDCI data from json_ext.

        Args:
            json_ext: Individual.json_ext dict
            registry_type: Optional registry type to validate against

        Returns:
            dict: SPDCI data or empty dict
        """
        if not json_ext or not isinstance(json_ext, dict):
            return {}

        # Validate registry type if provided
        if registry_type:
            stored_type = json_ext.get("registry_type")
            if stored_type != registry_type:
                return {}

        return json_ext.get("spdci_data", {})

    @staticmethod
    def update_json_ext(
        current_json_ext: Dict,
        spdci_person: Dict,
        registry_type: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Update existing json_ext with new SPDCI data.

        Args:
            current_json_ext: Current json_ext dict
            spdci_person: New SPDCI Person data
            registry_type: Registry type
            metadata: Optional metadata update

        Returns:
            dict: Updated json_ext
        """
        # Start with current data or create new structure
        if not current_json_ext or not isinstance(current_json_ext, dict):
            return JsonExtManager.structure_json_ext(registry_type, spdci_person, metadata)

        # Update registry metadata
        if not metadata:
            metadata = {}

        current_json_ext["registry_type"] = registry_type

        # Update metadata
        current_metadata = current_json_ext.get("registry_metadata", {})
        current_metadata.update({
            "source_system": metadata.get("source_system", current_metadata.get("source_system", "")),
            "external_id": metadata.get("external_id", current_metadata.get("external_id", "")),
            "last_sync": datetime.utcnow().isoformat() + 'Z'
        })
        current_json_ext["registry_metadata"] = current_metadata

        # Extract and update registry-specific data
        kind = str(registry_type or "").upper()
        if kind == "FR":
            new_spdci_data = JsonExtManager._extract_fr_data(spdci_person)
        elif kind == "SR":
            new_spdci_data = JsonExtManager._extract_sr_data(spdci_person)
        elif kind == "IBR":
            new_spdci_data = JsonExtManager._extract_ibr_data(spdci_person)
        else:
            new_spdci_data = spdci_person

        # Merge with existing spdci_data (deep merge for nested dicts)
        current_spdci_data = current_json_ext.get("spdci_data", {})
        current_spdci_data.update(new_spdci_data)
        current_json_ext["spdci_data"] = current_spdci_data

        return current_json_ext

    @staticmethod
    def validate_json_ext_structure(json_ext: Dict, registry_type: str) -> tuple[bool, Optional[str]]:
        """
        Validate json_ext structure for registry type.

        Args:
            json_ext: json_ext dict to validate
            registry_type: Expected registry type

        Returns:
            tuple: (is_valid, error_message)
        """
        if not json_ext or not isinstance(json_ext, dict):
            return False, "json_ext must be a dictionary"

        # Check required keys
        if "registry_type" not in json_ext:
            return False, "Missing 'registry_type' in json_ext"

        if json_ext["registry_type"] != registry_type:
            return False, f"Registry type mismatch: expected {registry_type}, got {json_ext['registry_type']}"

        if "registry_metadata" not in json_ext:
            return False, "Missing 'registry_metadata' in json_ext"

        if "spdci_data" not in json_ext:
            return False, "Missing 'spdci_data' in json_ext"

        return True, None
