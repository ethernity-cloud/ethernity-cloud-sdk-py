# PEP 563: make annotations lazy strings so builtin-generic subscripts like
# list[str] (PEP 585, Python 3.9+) don't get evaluated at class-definition time.
# The enclave base image is Python 3.7, where `list[str]` raises
# "TypeError: 'type' object is not subscriptable" and kills securelock at import
# (the enclave then exits before emitting its public key).
from __future__ import annotations

import os

from minio import Minio
from minio.error import S3Error


class SwiftStreamService:

    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        # Bound the HTTP timeouts. In the public-key extraction environment there
        # is NO etny-swift-stream (MinIO) container, so the first .env fetch would
        # otherwise block on a TCP connect to a non-existent host indefinitely
        # (MinIO's default client has effectively unbounded connect/read timeouts),
        # hanging the enclave after it has already emitted its PUBLIC_CERT. A short
        # timeout makes that call fail fast so securelock can exit cleanly in
        # cert-harvest mode. Real task runs reach SwiftStream in well under this.
        import urllib3
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=10.0, read=30.0),
            retries=urllib3.Retry(total=2, backoff_factor=0.5),
        )
        self.client = Minio(endpoint=endpoint,
                            access_key=access_key,
                            secret_key=secret_key,
                            secure=False,
                            http_client=http_client)

    def create_bucket(self, bucket_name: str) -> (bool, str):
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
            else:
                return False, f"Bucket, {bucket_name} already exists!"
        except S3Error as err:
            return False, err

        return True, f"Bucket, {bucket_name} successfully created!"

    def delete_bucket(self, bucket_name: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                file_objects = self.client.list_objects(bucket_name)
                if file_objects.size() == 0:
                    self.client.remove_bucket(bucket_name)
                else:
                    for file_object in file_objects:
                        self.delete_file(bucket_name, file_object)
                    self.client.remove_bucket(bucket_name)
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            return False, err

        return True, f"Bucket, {bucket_name} successfully deleted!"

    def delete_file(self, bucket_name: str, file_name: str) -> (bool, str):
        try:
            self.client.remove_object(bucket_name, file_name)
        except S3Error as err:
            return False, err

        return True, f"File, {file_name} successfully deleted!"

    def delete_files(self, bucket_name: str, list_of_files: list[str]) -> (bool, str):
        try:
            errors = self.client.remove_objects(bucket_name, list_of_files)
            for error in errors:
                print(f"[Error] Error occurred when deleting file: {error}!")
        except S3Error as err:
            return False, err

        return True, f"Files, {list_of_files} successfully deleted!"

    def upload_file(self, bucket_name: str, file_name: str, file_path: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                self.client.fput_object(bucket_name, file_name, file_path)
            else:
                self.create_bucket(bucket_name)
                self.client.fput_object(bucket_name, file_name, file_path)
        except S3Error as err:
            return False, err

        return True, f"{file_path} is successfully uploaded to bucket {bucket_name}."

    def upload_files(self, bucket_name: str, list_of_files: list[str],
                     upload_file_paths: list[str]) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                for file_idx in range(len(upload_file_paths)):
                    self.client.fput_object(bucket_name,
                                            list_of_files[file_idx],
                                            upload_file_paths[file_idx])
            else:
                self.create_bucket(bucket_name)
                for file_idx in range(len(upload_file_paths)):
                    self.client.fput_object(bucket_name,
                                            list_of_files[file_idx],
                                            upload_file_paths[file_idx])
        except S3Error as err:
            return False, err

        return True, f"{upload_file_paths} are successfully uploaded to bucket {bucket_name}."

    def download_file(self, bucket_name: str, file_name: str, file_path: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                self.client.fget_object(bucket_name, file_name, file_path)
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            return False, err

        return True, f"File, {file_name} from bucket {bucket_name} was downloaded in {file_path}."

    def download_files(self, bucket_name: str, list_of_files: list[str],
                       download_file_paths: list[str]) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                for file_idx in range(len(list_of_files)):
                    self.client.fget_object(bucket_name,
                                            list_of_files[file_idx],
                                            download_file_paths[file_idx])
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            return False, err

        return True, f"{download_file_paths} are successfully uploaded to bucket {bucket_name}."

    def get_file_content_bytes(self, bucket_name: str, file_name: str) -> (bool, bytes):
        response = None
        try:
            response = self.client.get_object(bucket_name, file_name)
            _d = b''
            for data in response.stream(amt=1024 * 1024):
                _d = _d + data

        except S3Error as err:
            return False, err
        finally:
            if response is not None:
                response.close()
                response.release_conn()
        return True, _d

    def get_file_content(self, bucket_name: str, file_name: str) -> (bool, str):
        status, content = self.get_file_content_bytes(bucket_name, file_name)
        if status:
            return True, content.decode('utf-8')
        return status, content

    def put_file_content(self, bucket_name: str, object_name: str, object_path: str,
                         object_data: object = None) -> (bool, str):
        try:
            if object_data is not None:
                self.client.put_object(bucket_name,
                                       object_name,
                                       object_data,
                                       len(object_data.getbuffer()))
            else:
                object_stat = os.stat(object_path)
                with open(object_path, 'rb') as file_data:
                    self.client.put_object(bucket_name,
                                           object_name,
                                           file_data,
                                           object_stat.st_size)
                file_data.close()
        except S3Error as err:
            return False, err

        return True, f"{object_name} is successfully uploaded to bucket {bucket_name}."

    def is_object_in_bucket(self, bucket_name: str, object_name: str) -> (bool, str):
        found = False
        try:
            result = self.client.stat_object(bucket_name, object_name)
            if result:
                found = True
        except S3Error as err:
            return False, err

        if found:
            return found, f"{object_name} exists inside {bucket_name}."
        else:
            return found, f"{object_name} doesn't exists inside {bucket_name}."

    def _list_buckets(self) -> None:
        buckets = self.client.list_buckets()
        for bucket in buckets:
            print(bucket.name, bucket.creation_date)

    def _list_objects(self, bucket_name: str) -> None:
        objects = self.client.list_objects(bucket_name)
        for obj in objects:
            print("-> ", obj.object_name, obj.owner_name)

    def _is_bucket(self, bucket_name: str) -> bool:
        return self.client.bucket_exists(bucket_name)
