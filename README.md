# Employee Attendance & Analytics API

A backend API for employee attendance management and analytics, built with **Python, FastAPI, and MongoDB** as part of the HROne Software Engineer Trainee technical assignment.

## Overview

The application manages employee records, attendance punch-in and punch-out, manual attendance corrections with an audit trail, and attendance analytics using MongoDB aggregation pipelines.

The API follows the requirements and business rules specified in the assignment's `PROBLEM_STATEMENT.docx`, `openapi.yaml`, and `DATA_MODEL.md`.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo or Motor, depending on the implementation
- Uvicorn
- Git and GitHub

## Features

The assignment requires the following functionality:

### Employee Management
- Create employee records.
- List employees with optional department filtering.
- Support pagination and sorting by employee code.
- Handle duplicate employee codes.

### Attendance Management
- Punch-in with employee status and timestamp validation.
- Punch-out with work-hours, overtime, and half-day calculations.
- Retrieve attendance records.
- List attendance records with employee, date, and status filters.
- Regularize attendance records with an audit history.

### Analytics
- Monthly attendance summary for an employee.
- Department-wise attendance summary.
- Late-comer leaderboard with correct ranking for ties.
- Department attendance trends with missing dates filled and a seven-day moving average.

### Administration and Performance
- Health check for MongoDB connectivity.
- MongoDB indexes created at application startup.
- Explain endpoint for inspecting query execution plans.

> Note: These are the assignment's required features. Verify each feature against the actual implementation before describing it as completed.

## Project Structure

```text
Hrone-Attendance-Api/
├── app/
│   └── main.py
├── requirements.txt
├── REVIEW.md
├── DECISIONS.md
├── README.md
├── PROBLEM_STATEMENT.docx
├── DATA_MODEL.md
├── openapi.yaml
├── sample_data/
└── sample_seed.py
```

The final submission should include the files required by HROne. Include supporting candidate-kit files only when appropriate and permitted. Do not commit secrets or environment files.

## Prerequisites

Install the following before running the application:

- Python 3.11 or newer
- MongoDB 6.0 or newer, locally or through MongoDB Atlas
- Git

## Setup and Installation

### 1. Clone the repository

```bash
git clone https://github.com/swapnilk1806/Hrone-Attendance-Api.git
cd Hrone-Attendance-Api
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux or macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Environment Configuration

Configure the MongoDB connection through environment variables:

- `MONGO_URI`: MongoDB connection URI.
- `MONGO_DB`: MongoDB database name.

**Windows PowerShell example:**

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="hrone_attendance"
```

**Linux or macOS example:**

```bash
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="hrone_attendance"
```

For MongoDB Atlas, use the connection URI from your own cluster.

Do not commit credentials, passwords, or `.env` files to GitHub. Environment variables supplied by the deployment or evaluation environment must take precedence over local configuration.

## Seed Sample Data

If the provided sample data and seed script are available, and the script is compatible with your database configuration, load the sample records with:

```bash
python sample_seed.py
```

The assignment's evaluator may supply its own dataset. The application must not depend on manually seeding the sample data to start.

## Run the Application

From the repository root, run:

```bash
uvicorn app.main:app --port 8000
```

For local development with automatic reloading:

```bash
uvicorn app.main:app --port 8000 --reload
```

The API should be available at:

- **Swagger UI:** http://127.0.0.1:8000/docs
- **ReDoc:** http://127.0.0.1:8000/redoc
- **Health check:** http://127.0.0.1:8000/health

The application must start using the command specified in the assignment.

## API Endpoints

The required API includes the following endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check application and database health |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List and filter employees |
| POST | `/attendance/punch-in` | Record employee punch-in |
| POST | `/attendance/punch-out` | Record employee punch-out |
| GET | `/attendance` | List attendance records |
| GET | `/attendance/{emp_code}/{date_text}` | Retrieve an attendance record |
| PATCH | `/attendance/{emp_code}/{date_text}` | Regularize an attendance record |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly employee analytics |
| GET | `/analytics/departments/summary` | Department attendance summary |
| GET | `/analytics/leaderboard/late` | Late-comer leaderboard |
| GET | `/analytics/departments/{department}/trend` | Department attendance trend |
| GET | `/admin/explain/{endpoint}` | Inspect query execution plans |

Use Swagger UI to inspect request schemas, parameters, and responses supported by the running application.

## Business Rules

The implementation should follow the assignment's rules, including:

- Attendance dates and shift times use Indian Standard Time (IST).
- API timestamps use integer epoch milliseconds.
- Punch-in timestamps are truncated to whole seconds before calculations.
- A ten-minute grace period applies to late arrivals.
- Overtime is counted when the applicable threshold is met.
- Work hours are rounded to two decimal places using half-up rounding.
- Half-day status is derived from the rounded work-hours value.
- Present-day calculations distinguish weekdays, weekends, and employee joining dates.
- Manual corrections append audit-history entries.
- Pagination starts at page 1, with a default page size of 20 and maximum of 100.
- Concurrent requests must not create duplicate attendance records or lose correction history.

Refer to the assignment specification for the complete rules and edge cases.

## Testing

Before submitting, verify the application locally.

1. Confirm that the application starts from a clean environment.
2. Check MongoDB connectivity using `/health`.
3. Test employee creation and duplicate employee codes.
4. Test valid and invalid punch-in and punch-out requests.
5. Verify work-hours, overtime, and half-day calculations.
6. Test manual corrections and audit-history preservation.
7. Test pagination and filtering.
8. Verify analytics for employees with no attendance logs, ties, weekend records, overnight shifts, and missing dates.
9. Test concurrent requests.
10. Inspect query plans and confirm that appropriate indexes are used.

Only report tests as passed when you have actually run them.

## Review and Design Decisions

- `REVIEW.md` documents defects identified in the starter application, their symptoms, and the fixes applied.
- `DECISIONS.md` explains the index design, concurrent punch-in handling, leaderboard tie behavior, department headcount calculation, and scalability considerations.

## Known Limitations

Document any incomplete endpoints, failing tests, or other known limitations here before submitting. Do not claim full compliance if any required functionality remains unfinished.

## Submission

This repository is prepared for the HROne Software Engineer Trainee technical assignment.

- **GitHub repository:** https://github.com/swapnilk1806/Hrone-Attendance-Api
- **Required files:** `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md`, and `README.md`
- **Submission method:** Submit the public repository URL through the Microsoft Form provided in the HROne assignment email.
- **Deadline:** 11:59 PM IST, 15 October 2026.

Keep the repository public until 31 October 2026, as requested by HROne.

## Author

Swapnil Kadam
