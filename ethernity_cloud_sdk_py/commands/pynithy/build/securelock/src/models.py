"""
Module: models.py

This module defines data models and factories for handling orders and metadata in a blockchain-based task processing system. It includes classes for orders, base metadata, versioned payload and input metadata (V0 and V3), and factories to create metadata objects based on version strings. Additionally, it provides a class for DO (Data Owner?) request metadata, parsing and exposing properties like image hash, public key, and node address. The models support versioning, with V3 including checksums (potentially signed) for integrity verification.

Key Features:
- Order model: Represents blockchain orders with attributes like owner, processor, requests, and status.
- Metadata base and subclasses: Abstract base for metadata with version, IPFS hash, and optional checksum; V0 is hash-only, V3 includes checksum.
- Factories: Dynamically create versioned metadata objects from strings (e.g., "v3:hash:checksum").
- DOReqMetadata: Parses request metadata into accessible properties, integrating with factories for payload/input objects.
- No external dependencies beyond standard Python.

Usage Context: Used in trustedzone.py to fetch and parse order/request metadata from smart contracts, enabling validation and processing in secure environments like Ethernity/Etny.

Potential Security Notes: Checksums in V3 can be signed (0x-prefixed), but validation logic is external (e.g., in trustedzone.py). Assumes metadata strings are trusted from blockchain; malformed inputs could raise ValueError.
"""

class Order:
    def __init__(self, req, order_id):
        self.id = order_id
        self.do_owner = req[0]
        self.dproc = req[1]
        self.do_req = req[2]
        self.dp_req = req[3]
        self.status = req[4]


class MetadataBase:
    def __init__(self, metadata, version):
        self.metadata = metadata
        self._version = version

    @property
    def version(self):
        return self._version

    @property
    def ipfs_hash(self):
        raise NotImplementedError("Subclass must implement this method")

    def checksum(self):
        raise NotImplementedError("Subclass must implement this method")


class PayloadFactory:
    @staticmethod
    def create_payload_metadata(metadata):
        if ':' in metadata:
            if metadata.startswith('v3') or metadata.startswith('v4'):
                return PayloadMetadata(metadata)
            else:
                raise ValueError("Invalid payload metadata type")
        else:
            return PayloadMetadataV0(metadata)


class InputFactory:
    @staticmethod
    def create_input_metadata(metadata):
        if ':' in metadata:
            if metadata.startswith('v3'):
                return InputMetadatav3(metadata)
            else:
                raise ValueError("Invalid payload metadata type")
        else:
            return InputMetadataV0(metadata)


class InputMetadataV0(MetadataBase):
    def __init__(self, metadata):
        super().__init__(metadata, 'v0')
        self._ipfs_hash = metadata

    @property
    def ipfs_hash(self):
        return self._ipfs_hash

    @property
    def checksum(self):
        return None


class PayloadMetadataV0(MetadataBase):
    def __init__(self, metadata):
        super().__init__(metadata, 'v0')
        self._ipfs_hash = metadata

    @property
    def ipfs_hash(self):
        return self._ipfs_hash

    @property
    def checksum(self):
        return None


class PayloadMetadata(MetadataBase):

    def __init__(self, metadata):
        super().__init__(metadata, metadata.split(':')[0])
        self._checksum = metadata.split(':')[2]
        self._ipfs_hash = metadata.split(':')[1]
        

    @property
    def ipfs_hash(self):
        return self._ipfs_hash

    @property
    def checksum(self):
        return self._checksum


class InputMetadatav3(MetadataBase):

    def __init__(self, metadata):
        super().__init__(metadata, 'v3')
        self._checksum = metadata.split(':')[2]
        self._ipfs_hash = metadata.split(':')[1]

    @property
    def ipfs_hash(self):
        return self._ipfs_hash

    @property
    def checksum(self):
        return self._checksum if self._checksum != '0' else self._checksum


class DOReqMetadata:
    def __init__(self, req, do_req):
        self._do_req_id = do_req
        self._do_owner = req[0]
        self._metadata1 = req[1]
        self._metadata2 = req[2]
        self._metadata3 = req[3]
        self._metadata4 = req[4]
        self._payload_metadata_obj = PayloadFactory.create_payload_metadata(self.payload_metadata)
        self._input_metadata_obj = InputFactory.create_input_metadata(self.input_metadata)

    @property
    def do_req_id(self):
        return self._do_req_id

    @property
    def do_owner(self):
        return self._do_owner

    @property
    def image_metadata(self):
        return self._metadata1

    @property
    def public_key(self):
        return self.image_metadata.split(':')[5]

    @property
    def image_hash(self):
        return self.image_metadata.split(':')[1]

    @property
    def trustedzone_image_name(self):
        return self.image_metadata.split(':')[2]

    @property
    def payload_metadata(self):
        return self._metadata2

    @property
    def payload_metadata_obj(self):
        return self._payload_metadata_obj

    @property
    def input_metadata_obj(self):
        return self._input_metadata_obj

    @property
    def input_metadata(self):
        return self._metadata3

    @property
    def node_address(self):
        return self._metadata4

