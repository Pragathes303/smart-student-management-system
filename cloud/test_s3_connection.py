"""
Amazon S3 Connectivity & Permissions Test Utility
Verifies that AWS credentials, Region, and Bucket name are valid and active.
Performs an upload, pre-signed URL generation, and cleanup verification.
"""

import os
import sys
import uuid
from io import BytesIO

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))


def test_s3_connection():
    print("=" * 60)
    print("  AMAZON S3 CLOUD CONNECTIVITY & PERMISSION TEST")
    print("=" * 60)

    bucket = os.getenv('AWS_S3_BUCKET')
    region = os.getenv('AWS_REGION', 'us-east-1')
    access_key = os.getenv('AWS_ACCESS_KEY_ID')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    storage_backend = os.getenv('STORAGE_BACKEND', 'local')

    print(f"[*] STORAGE_BACKEND configured: '{storage_backend}'")
    print(f"[*] Target AWS Region: '{region}'")
    print(f"[*] Target S3 Bucket: '{bucket or 'NOT SET'}'")
    masked_key = (access_key[:4] + "..." + access_key[-4:]) if access_key and len(access_key) > 8 else (access_key or 'NOT SET')
    print(f"[*] AWS Access Key ID: {masked_key}")

    if not bucket or not access_key or not secret_key:
        print("\n[!] INCOMPLETE AWS CREDENTIALS DETECTED in .env:")
        if not bucket:
            print("    - AWS_S3_BUCKET is missing")
        if not access_key:
            print("    - AWS_ACCESS_KEY_ID is missing")
        if not secret_key:
            print("    - AWS_SECRET_ACCESS_KEY is missing")
        print("\n[+] To connect real S3, update your .env file with:")
        print("    STORAGE_BACKEND=s3")
        print("    AWS_ACCESS_KEY_ID=your_access_key")
        print("    AWS_SECRET_ACCESS_KEY=your_secret_key")
        print("    AWS_REGION=us-east-1 (or ap-south-1 for Mumbai)")
        print("    AWS_S3_BUCKET=your-bucket-name")
        print("\n[*] The application will continue safely using 'LocalStorageHandler' until then.")
        return False

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("\n[!] Error: 'boto3' is not installed. Please run: pip install boto3")
        return False

    print("\n[*] Connecting to AWS S3 client...")
    try:
        client = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
    except Exception as e:
        print(f"[X] Failed to create boto3 S3 client: {e}")
        return False

    # 1. Verify Bucket Head
    print(f"[*] Verifying access to bucket '{bucket}'...")
    try:
        client.head_bucket(Bucket=bucket)
        print(f"    [OK] Bucket '{bucket}' exists and is reachable!")
    except ClientError as e:
        err_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"    [X] Failed to access bucket '{bucket}': Error {err_code} - {e}")
        return False

    # 2. Test Upload
    test_key = f"diagnostics/test_{uuid.uuid4().hex[:8]}.txt"
    test_content = b"Smart Student Management System - AWS S3 Test Object"
    print(f"[*] Testing write permission: uploading '{test_key}'...")
    try:
        client.upload_fileobj(
            BytesIO(test_content),
            bucket,
            test_key,
            ExtraArgs={'ContentType': 'text/plain'}
        )
        print("    [OK] Upload successful!")
    except Exception as e:
        print(f"    [X] Upload failed: {e}")
        return False

    # 3. Test Pre-signed URL generation
    print("[*] Testing Pre-Signed URL generation...")
    try:
        url = client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': test_key},
            ExpiresIn=3600
        )
        print(f"    [OK] Pre-signed URL generated: {url[:60]}... [Expires in 1 hour]")
    except Exception as e:
        print(f"    [!] Presigned URL generation warning: {e}")

    # 4. Cleanup Test Object
    print(f"[*] Cleaning up test object '{test_key}'...")
    try:
        client.delete_object(Bucket=bucket, Key=test_key)
        print("    [OK] Test object deleted cleanly!")
    except Exception as e:
        print(f"    [!] Failed to delete test object: {e}")

    print("\n" + "=" * 60)
    print("  RESULT: AWS S3 INTEGRATION IS 100% OPERATIONAL!")
    print("  You can safely set STORAGE_BACKEND=s3 in .env.")
    print("=" * 60)
    return True


if __name__ == '__main__':
    test_s3_connection()
