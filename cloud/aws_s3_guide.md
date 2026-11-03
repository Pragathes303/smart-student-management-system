# Amazon S3 Integration Guide for Smart Student Management System

This document explains how to connect **Amazon Simple Storage Service (Amazon S3)** to this application when you are ready to transition from local file storage to the AWS Cloud.

---

## 1. Storage Architecture Overview

The system is designed with a **pluggable Storage Abstraction Layer** (`backend/storage.py`):

```
                        +------------------------------------+
                        |        Application Routes          |
                        | (routes.py: add, edit, delete, view)|
                        +-----------------+------------------+
                                          |
                                          v
                                +-------------------+
                                | get_storage()     |
                                +---------+---------+
                                          |
                     +--------------------+--------------------+
                     |                                         |
                     v                                         v
         +-----------------------+                 +-----------------------+
         |  LocalStorageHandler  |                 |    S3StorageHandler   |
         | (Local static/uploads)|                 |  (Amazon S3 Bucket)   |
         +-----------------------+                 +-----------------------+
```

Because of this separation of concerns:
- **Routes never call filesystem APIs directly.**
- You can toggle between **Local Filesystem** and **Amazon S3** by simply updating a single variable in your `.env` file (`STORAGE_BACKEND=local` vs `STORAGE_BACKEND=s3`).
- Zero changes to your application code are required.

---

## 2. Step-by-Step AWS Setup Instructions

### Step 1: Create an Amazon S3 Bucket
1. Log in to the [AWS Management Console](https://console.aws.amazon.com/).
2. In the search bar, type **S3** and open the S3 console.
3. Click **Create bucket**.
4. Configure the bucket:
   - **Bucket name**: e.g., `student-ms-assets-<your-unique-id>` (must be globally unique).
   - **AWS Region**: e.g., `us-east-1` (US East, N. Virginia) or your closest region.
   - **Object Ownership**: ACLs disabled (recommended).
5. Click **Create bucket** at the bottom.

### Step 2: Create an IAM User with S3 Permissions
1. In the AWS Console, search for **IAM** (Identity and Access Management).
2. Go to **Users** &rarr; **Create user**.
3. Set the username (e.g., `student-system-s3-uploader`).
4. Select **Attach policies directly** and attach either:
   - `AmazonS3FullAccess` (for testing/development), OR
   - A custom least-privilege policy (recommended for production):
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [
         {
           "Effect": "Allow",
           "Action": [
             "s3:PutObject",
             "s3:GetObject",
             "s3:DeleteObject",
             "s3:ListBucket"
           ],
           "Resource": [
             "arn:aws:s3:::your-bucket-name",
             "arn:aws:s3:::your-bucket-name/*"
           ]
         }
       ]
     }
     ```
5. Click **Create user**.
6. Open the newly created user &rarr; Navigate to the **Security credentials** tab.
7. Click **Create access key** &rarr; Select **Application running outside AWS** (or on EC2).
8. Copy the **Access Key ID** and **Secret Access Key** (save them securely).

### Step 3: Install `boto3` on Your Server / Environment
Run:
```bash
pip install boto3
```

### Step 4: Configure the `.env` File
Open your `.env` file and update the storage section:

```env
# Switch storage from 'local' to 's3'
STORAGE_BACKEND=s3

# Fill in your AWS credentials
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_REGION=us-east-1
AWS_S3_BUCKET=student-ms-assets-your-unique-id
```

### Step 5: Restart the Application
- If running locally:
  ```bash
  python run.py
  ```
- If running on AWS EC2 with Gunicorn & Systemd:
  ```bash
  sudo systemctl restart student_system
  ```

---

## 3. Verifying S3 Uploads
1. Go to the student management system in your browser.
2. Navigate to **+ Add Student**.
3. Fill out the student details and select a student photo.
4. Click **Save Student Record**.
5. Check your Amazon S3 Console:
   - Navigate into your bucket.
   - You will see the uploaded photo organized under the `student-photos/` prefix!
6. View the student profile in the web app: the image will be loaded directly from Amazon S3.

---

## 4. Security Recommendations for AWS
- **Never commit `.env` or AWS keys to Git.** The `.gitignore` file is already pre-configured to ignore `.env`.
- If deploying to AWS EC2, you can also use **IAM EC2 Instance Roles** instead of hardcoded access keys. `boto3` will automatically detect instance roles.
- Use Amazon CloudFront as a CDN in front of S3 for caching and HTTPS delivery.
