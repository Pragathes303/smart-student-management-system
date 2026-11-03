"""
Storage Abstraction Layer
Decouples file storage from application routes so the system can seamlessly
switch between Local Storage (for local development) and Amazon S3 (for AWS cloud deployment).
"""

import os
import uuid

from flask import current_app, url_for
from werkzeug.utils import secure_filename


class BaseStorageHandler:
    """Abstract interface defining required storage operations."""

    def save(self, file_storage):
        """Save an uploaded file and return its unique identifier or key."""
        raise NotImplementedError

    def delete(self, identifier):
        """Delete a file using its identifier. Returns True if deleted or not found."""
        raise NotImplementedError

    def get_url(self, identifier):
        """Return a web-accessible URL to display or download the file."""
        raise NotImplementedError

    def is_cloud_active(self):
        """Return True if cloud storage (S3) is actively handling files."""
        return False


class LocalStorageHandler(BaseStorageHandler):
    """
    Local filesystem storage handler.
    Stores files in frontend/static/uploads and generates local static URLs.
    """

    def __init__(self, upload_folder):
        self.upload_folder = upload_folder
        os.makedirs(self.upload_folder, exist_ok=True)

    def save(self, file_storage):
        if not file_storage or not file_storage.filename:
            return None

        clean_name = secure_filename(file_storage.filename)
        if not clean_name:
            clean_name = "upload.jpg"
        # Prefix with random hex to avoid filename collisions
        unique_name = f"{uuid.uuid4().hex[:12]}_{clean_name}"
        destination = os.path.join(self.upload_folder, unique_name)

        if hasattr(file_storage, 'save'):
            file_storage.save(destination)
        elif hasattr(file_storage, 'getvalue'):
            with open(destination, 'wb') as f:
                f.write(file_storage.getvalue())
        elif hasattr(file_storage, 'read'):
            with open(destination, 'wb') as f:
                f.write(file_storage.read())
        if current_app:
            current_app.logger.info("Local storage: saved file '%s' to '%s'", unique_name, destination)
        return unique_name

    def delete(self, identifier):
        if not identifier:
            return True
        file_path = os.path.join(self.upload_folder, identifier)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                if current_app:
                    current_app.logger.info("Local storage: removed file '%s'", file_path)
                return True
            except OSError as e:
                if current_app:
                    current_app.logger.error("Local storage error deleting '%s': %s", file_path, str(e))
                return False
        return True

    def get_url(self, identifier):
        if not identifier:
            return None
        # Handle ephemeral filesystems on free cloud hosts (e.g. Render restarts)
        # If the file was erased during container rebuild/restart, return None so UI renders avatar initials cleanly
        file_path = os.path.join(self.upload_folder, identifier)
        if not os.path.exists(file_path):
            return None

        # Serve via Flask static endpoint
        try:
            return url_for('static', filename=f'uploads/{identifier}')
        except RuntimeError:
            return f"/static/uploads/{identifier}"

    def is_cloud_active(self):
        return False


class S3StorageHandler(BaseStorageHandler):
    """
    Amazon Web Services (AWS) Simple Storage Service (S3) Handler.
    Ready to connect when AWS credentials and bucket name are configured in .env.
    """

    def __init__(self, bucket_name, region, access_key, secret_key):
        self.bucket_name = bucket_name
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self._s3_client = None

    def _get_client(self):
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client(
                    's3',
                    region_name=self.region,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key
                )
            except ImportError:
                raise RuntimeError(
                    "boto3 package is not installed. To activate Amazon S3, run: pip install boto3"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to initialize AWS S3 client: {e!s}")
        return self._s3_client

    def save(self, file_storage):
        if not file_storage or not file_storage.filename:
            return None

        clean_name = secure_filename(file_storage.filename)
        key = f"student-photos/{uuid.uuid4().hex[:8]}_{clean_name}"
        content_type = file_storage.content_type or 'application/octet-stream'

        client = self._get_client()
        try:
            client.upload_fileobj(
                file_storage,
                self.bucket_name,
                key,
                ExtraArgs={'ContentType': content_type}
            )
            if current_app:
                current_app.logger.info("AWS S3: uploaded file key '%s' to bucket '%s'", key, self.bucket_name)
            return key
        except Exception as e:
            if current_app:
                current_app.logger.error("AWS S3 upload error: %s", str(e))
            raise RuntimeError(f"Amazon S3 upload failed: {e!s}")

    def delete(self, identifier):
        if not identifier:
            return True
        try:
            client = self._get_client()
            client.delete_object(Bucket=self.bucket_name, Key=identifier)
            if current_app:
                current_app.logger.info("AWS S3: deleted key '%s' from bucket '%s'", identifier, self.bucket_name)
            return True
        except Exception as e:
            if current_app:
                current_app.logger.error("AWS S3 delete error: %s", str(e))
            return False

    def get_url(self, identifier, expires_in=3600):
        if not identifier:
            return None
        # If public read mode is explicitly set in .env
        if os.getenv('AWS_S3_PUBLIC_READ', '0') == '1':
            return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{identifier}"

        # Standard S3 security: Generate pre-signed URL (valid for 1 hour)
        try:
            client = self._get_client()
            return client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': identifier},
                ExpiresIn=expires_in
            )
        except Exception as e:
            if current_app:
                current_app.logger.warning("Could not generate presigned S3 URL: %s, falling back to direct URL", str(e))
            return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{identifier}"

    def is_cloud_active(self):
        return True


def get_storage():
    """
    Factory function returning the active storage handler based on application configuration.
    Defaults to LocalStorageHandler.
    """
    from backend.config import Config

    backend_type = current_app.config.get('STORAGE_BACKEND', Config.STORAGE_BACKEND) if current_app else Config.STORAGE_BACKEND

    if backend_type == 's3':
        bucket = current_app.config.get('AWS_S3_BUCKET', Config.AWS_S3_BUCKET) if current_app else Config.AWS_S3_BUCKET
        region = current_app.config.get('AWS_REGION', Config.AWS_REGION) if current_app else Config.AWS_REGION
        access_key = current_app.config.get('AWS_ACCESS_KEY_ID', Config.AWS_ACCESS_KEY_ID) if current_app else Config.AWS_ACCESS_KEY_ID
        secret_key = current_app.config.get('AWS_SECRET_ACCESS_KEY', Config.AWS_SECRET_ACCESS_KEY) if current_app else Config.AWS_SECRET_ACCESS_KEY

        if not bucket or not access_key or not secret_key:
            # Fall back to local with warning if S3 credentials are unset
            if current_app:
                current_app.logger.warning(
                    "STORAGE_BACKEND is 's3' but AWS credentials/bucket are incomplete in .env. Falling back to LocalStorageHandler."
                )
            return LocalStorageHandler(current_app.config.get('UPLOAD_FOLDER', Config.UPLOAD_FOLDER) if current_app else Config.UPLOAD_FOLDER)

        return S3StorageHandler(bucket, region, access_key, secret_key)

    # Local Storage
    upload_dir = current_app.config.get('UPLOAD_FOLDER', Config.UPLOAD_FOLDER) if current_app else Config.UPLOAD_FOLDER
    return LocalStorageHandler(upload_dir)


# Backward-compatible alias
get_storage_handler = get_storage

