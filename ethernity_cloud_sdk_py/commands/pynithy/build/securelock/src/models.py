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
            if metadata.startswith('v3'):
                return PayloadMetadatav3(metadata)
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


class PayloadMetadatav3(MetadataBase):

    def __init__(self, metadata):
        super().__init__(metadata, 'v3')
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


# add_do_req(..., 'v3:image_:....:...:', 'v3:payload_hash:checksum', 'v3::0', 'node_address')
'''
input + payload
v0: 'ipfs_hash'
v3: 'v3:file_ipfs_hash:file_checksum'

image
v0: 'ipfs_hash:image_name'
v3: 'v3:image_ipfs_hash:image_name:docker_Compose_ipfs_hash:client_challenge_ipfs_hash:client_public_cert'
'''
if __name__ == '__main__':
    obj = PayloadFactory.create_payload_metadata('v3:some_img_hash:fucker')
    obj2 = PayloadFactory.create_payload_metadata('must not!')
