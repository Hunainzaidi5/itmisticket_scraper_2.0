# ITMIS Ticket Scraper

**ITMIS Ticket Scraper** is a desktop automation and ticket-analysis application designed to work with an ITMIS web-based service-ticket system. It provides two complementary workflows:

1. **Batch Ticket Scraping** — imports ticket numbers or ticket URLs from an Excel workbook, opens the ITMIS ticket pages through Selenium/Chrome, extracts ticket information, analyzes ticket text against configured keywords, evaluates closure timing, and exports structured results.
2. **Live Monitor** — continuously watches the ITMIS dashboard, notification bell, and browser tabs for newly appearing tickets, captures new ticket identifiers, opens detected tickets in separate tabs, extracts their full details, detects duplicates, and maintains a persistent monitoring session.

The application is implemented in Python with **PyQt6** for the desktop interface, **Selenium** for browser automation, **Pandas** for Excel/data processing, and **ChromeDriver/Selenium Manager** for browser control.

---

# Application Overview

The program is essentially an **ITMIS ticket automation workstation**.

Instead of manually opening each service ticket and copying information into an Excel sheet, the application automates the repetitive browser work.

For batch processing, the workflow is:

```text
Excel Input
    |
    v
Validate Ticket Numbers / URLs
    |
    v
Start Chrome + Selenium
    |
    v
Manual ITMIS Login
    |
    v
Open Ticket URL
    |
    v
Extract Ticket Details
    |
    +--> Description / Content
    +--> Comments
    +--> Station
    +--> Ticket Category
    +--> Resolution Time
    +--> Ticket Start Time
    +--> Resolved Date/Time
    +--> Last Comment Time
    |
    v
Keyword Analysis
    |
    v
Closure-Time Analysis
    |
    v
Structured Result
    |
    +--> rechecked.xlsx
    +--> timestamped backup
    +--> summary JSON
```

The live-monitor workflow is different:

```text
ITMIS Login
    |
    v
Live Monitor Patrol
    |
    +--> Dashboard monitoring
    +--> Notification-bell monitoring
    +--> Browser-tab monitoring
    |
    v
Detect New Ticket
    |
    v
Duplicate Check
    |
    v
Open Ticket in New Tab
    |
    v
Extract Full Details
    |
    v
Display Ticket Card / Session Statistics
    |
    v
Persist Session
```

The source implements the live-monitor patrol loop at a high frequency and separately refreshes dashboard information on a longer interval. The dashboard refresh interval is configured in the code as 10 seconds, while the patrol status reports a 500 ms polling cycle. fileciteturn1file3L293-L317

---

# Main Features

## Modern Desktop GUI

The application uses PyQt6 and provides a dedicated graphical interface rather than requiring command-line operation.

The interface uses a **glassmorphism / acrylic visual design** with:

- translucent panels
- rounded cards
- soft shadows
- glass-style dialogs
- Windows acrylic/Mica-style backdrop support
- light visual theme
- colored status indicators
- progress feedback
- ticket/session summary cards

The code attempts to use native Windows backdrop APIs for real blur-behind effects and falls back to transparency where native blur is unavailable.

The application is registered as:

- **Application:** ITMIS Ticket Scraper
- **Version:** 2.0
- **Organization:** TicketScraper

These properties are set during application startup. fileciteturn2file1L129-L165

---

# Batch Ticket Scraper

The batch scraper is intended for processing a prepared Excel list of tickets.

## Excel Input

The application accepts:

```text
.xlsx
.xls
```

Excel files.

The file-selection workflow reads the workbook with Pandas and attempts to identify the ticket/link column.

The primary expected column is:

```text
Link
```

The application can also search for a column whose name contains terms such as:

```text
ticket
number
```

and can convert ticket numbers into complete ITMIS ticket URLs. fileciteturn2file7L681-L710

---

# Ticket Number Validation

The application contains a dedicated ticket-number validation system.

The expected ticket structure is:

```text
LOCATION.LINE.YYYY.MM.REFERENCE
```

The validation logic enforces:

- location code: 2–5 letters
- system/line code: 2–8 alphanumeric characters
- year: 2000–2099
- month: 01–12
- reference number: 6–10 digits

The program therefore prevents malformed ticket identifiers from being sent into the browser automation process.

It can also recognize complete HTTP/HTTPS ticket URLs and extract ticket numbers from URL-like strings.

---

# Automatic Ticket URL Generation

If the Excel file contains a valid ticket number rather than a complete URL, the application generates the ticket URL using the configured base ticket URL.

Conceptually:

```text
Ticket Number
      |
      v
Base Ticket URL + Ticket Number
      |
      v
Full ITMIS Ticket URL
```

This allows the input workbook to contain simple ticket identifiers rather than manually generated hyperlinks.

---

# Chrome and Selenium Automation

The program uses:

- Selenium
- Chrome
- ChromeDriver
- Selenium Manager
- webdriver-manager

The browser setup has several fallback mechanisms.

### Browser startup strategy

The application first attempts to use:

**Selenium Manager**

If that fails, it falls back to:

**webdriver-manager**

If that also fails, it attempts to locate:

```text
chromedriver.exe
```

through the system `PATH`.

The application also creates a dedicated writable application directory under the user's local application-data location for browser profiles and WebDriver/Selenium caches. fileciteturn1file3L324-L358

This is particularly useful for packaged EXE deployments because WebDriver cache files need a writable location.

---

# Manual ITMIS Login

The scraper does not attempt to programmatically enter the user's credentials.

Instead, Chrome is opened at the configured ITMIS login URL and the user is asked to log in.

The application then waits for the ITMIS dashboard to appear as evidence that authentication has succeeded.

It additionally attempts to verify the browser session through the site's `localStorage`.

The login process has configurable retry behavior and waits for the dashboard before proceeding. 

---

# Ticket Data Extraction

For each ticket, the scraper navigates directly to the ticket URL and extracts information from the rendered ITMIS page.

The extracted fields include:

### Ticket content

The main ticket description/content is captured.

### Comments

Ticket comments are extracted separately and then combined with the ticket description for text analysis.

### Station Number

The station associated with the ticket is extracted.

### Ticket Category

The ticket category is extracted.

### Resolution Time

The displayed resolution time is captured.

### Ticket Start Time

The ticket start date/time is captured.

### Resolved Date Time

The resolved date/time is captured.

### Last Comment Time

The latest available comment timestamp is extracted.

These fields are explicitly represented in the result record generated by the scraper. fileciteturn1file7L629-L700

---

# XPath-Based Extraction

The application uses XPath selectors to locate elements inside the ITMIS web application.

The configuration contains selectors for:

```text
Content
Comments
Station
Resolution Time
Ticket Start Time
Resolved Date Time
Ticket Category
Last Comment Time
```

The scraper also includes fallback XPath handling.

For extraction, it can try:

1. visibility of the element
2. presence of the element
3. fallback XPath

This makes the extraction process more tolerant of minor page-loading differences.

---

# Keyword Analysis

One of the most useful analytical features is keyword detection.

The application combines:

```text
Ticket Content
+
Ticket Comments
```

into a single searchable text body.

It then compares this text against the configured keyword list.

For every matching keyword, the program records the keyword.

The output therefore includes:

```text
Contains Keywords
Keywords Found
Keyword Count
```

The keyword comparison is case-insensitive. fileciteturn1file5L457-L483

This can be used for operational searches such as identifying tickets mentioning particular equipment, faults, locations, components, or operational conditions.

---

# Closure-Time Analysis

The program also performs a basic closure-time check.

It extracts:

```text
Ticket Start Time
Last Comment Time
```

and calculates the difference between them.

The current implementation marks:

```text
Closed Within Time = True
```

when the calculated interval is **2 hours or less**.

This is an important implementation detail: the current source uses a fixed two-hour threshold for this calculation. fileciteturn1file5L485-L511

---

# Result Record

Each successfully processed ticket is converted into a structured dictionary.

The record contains fields including:

```text
Contains Keywords
URL
Check Time
Ticket Category
Content Length
Comment Length
Combined Length
Keywords Found
Keyword Count
Ticket Content
Ticket Comments
Station Number
Resolution Time
Ticket Start Time
Resolved Date Time
Last Comment Time
Closed Within Time
Processing Status
```

The processing status can distinguish successful and partial extraction.

The application therefore produces a much richer dataset than a simple list of ticket URLs. fileciteturn2file5L442-L480

---

# Retry and Error Handling

The scraper is designed to continue operating when individual tickets fail.

Configurable retry parameters include:

```text
Maximum Retries
Retry Delay
```

If a ticket produces a critical error, the program retries it.

If the browser session appears to have expired, the application can attempt to log in again before continuing.

If all attempts fail, the ticket is recorded as a failed/critical result and the scraper continues to the next ticket.

This prevents a single problematic ticket from terminating the entire batch.

---

# Progress Monitoring

The GUI provides real-time processing feedback.

During batch processing it displays:

- current processing status
- progress percentage
- activity log
- number of tickets processed
- completion state
- errors/warnings

The progress value is calculated from the current ticket index and total number of ticket URLs.

---

# Result Export

After scraping, the application saves the results using Pandas.

The primary output file is:

```text
rechecked.xlsx
```

A timestamped backup is also generated:

```text
rechecked_backup_YYYYMMDD_HHMMSS.xlsx
```

A JSON summary is generated as:

```text
summary_YYYYMMDD_HHMMSS.json
```

The implementation writes the main and backup Excel workbooks and then generates the summary JSON. fileciteturn2file5L482-L518

---

# Summary Statistics

The application calculates summary information such as:

- total tickets
- successful tickets
- failed tickets
- tickets containing configured keywords
- success rate
- percentage of successful tickets containing keywords

The GUI presents these statistics after a scraping session and provides a quick summary dashboard.

The completion interface also provides an option to open the generated `rechecked.xlsx` file directly. fileciteturn2file2L191-L252

---

# Live Monitor

The Live Monitor is a separate operational mode intended for continuous observation of ITMIS ticket activity.

It uses a dedicated `LiveMonitorThread` running independently from the main GUI thread.

The monitor can observe:

1. browser tabs
2. ITMIS dashboard
3. notification bell

The implementation maintains internal sets and mappings for processed ticket IDs, processed URLs, browser handles, duplicate notifications, and captured tickets. fileciteturn2file6L548-L582

---

# 19. Dashboard Monitoring

The live monitor periodically checks the ITMIS dashboard for newly appearing tickets.

The dashboard refresh interval is currently:

```text
10 seconds
```

When a new ticket is detected, the monitor:

1. identifies the ticket
2. checks whether it has already been processed
3. creates an initial ticket record
4. opens the ticket in a new browser tab
5. extracts the complete ticket information
6. upgrades/replaces the preliminary record with the full ticket data

This design prevents dashboard-row information from becoming the final incomplete ticket record. fileciteturn1file2L216-L270

---

# 20. Notification-Bell Monitoring

The application also watches the ITMIS notification bell.

When the notification count increases, it:

1. opens the notification dropdown
2. attempts to identify the ticket number
3. obtains the ticket URL
4. checks whether the ticket has already been processed
5. creates a preliminary record
6. opens the ticket in a new browser tab
7. performs full ticket extraction

This provides another detection path in addition to the dashboard.

The notification monitoring implementation explicitly avoids processing a ticket again when its ticket ID has already been captured. fileciteturn1file2L224-L267

---

# 21. Duplicate Detection

Live Monitor maintains processed ticket IDs and URLs.

This allows it to detect tickets that have already been captured.

The interface also tracks a statistic for:

```text
Duplicates Skipped
```

The monitoring architecture includes dedicated duplicate-detection signals and internal tracking structures. fileciteturn2file6L548-L582

---

# 22. New-Tab Full Extraction

When Live Monitor detects a ticket, it does not rely only on the dashboard or notification text.

Instead, it opens the actual ticket page in a new Chrome tab.

The full ticket extraction is then performed from the ticket page.

This is important because dashboard/notification records may only contain partial information.

The source explicitly describes this process as upgrading preliminary records into complete ticket-page records. fileciteturn1file9L839-L887

---

# 23. Live Monitor Session Persistence

Live Monitor sessions can be persisted.

When the application closes, the current live-monitor information is saved.

When the application starts again, it can detect a previous monitoring session and ask whether it should be restored.

Restored records can subsequently be re-extracted if they were only preliminary records and did not previously receive complete ticket-page details. fileciteturn1file6L535-L576

This makes the monitor more resilient to application restarts.

---

# 24. Live Monitor Dashboard

The Live Monitor interface provides operational session statistics such as:

```text
Tickets Captured
Duplicates Skipped
Browser Tabs
Session Start Time
```

It also provides a live ticket feed and monitoring status.

The source defines dedicated session-summary cells for captured tickets, duplicates, browser tabs, and session start time. fileciteturn2file3L311-L351

---

# 25. Batch Scraper and Live Monitor Mutual Exclusion

The application prevents the batch scraper and Live Monitor from simultaneously using the same Chrome resources.

If batch scraping is active, attempting to start Live Monitor produces a warning asking the user to stop the scraper first.

This avoids browser-driver contention and conflicting Selenium operations. fileciteturn2file4L389-L423

---

# 26. Configuration System

The application uses Qt's `QSettings` for persistent configuration.

Settings include:

### ITMIS URLs

```text
Login URL
Dashboard URL
Base Ticket URL
```

### Timing

```text
Login Timeout
Page Load Timeout
Element Wait Timeout
Delay Between Tickets
```

### Retry

```text
Maximum Retries
Retry Delay
```

### Browser

```text
Chrome Binary Path
Headless Mode
```

### Analysis

```text
Keyword List
```

The application stores these settings persistently so they do not have to be entered again each time the program starts.

---

# 27. Configurable Browser Mode

The application supports:

```text
Visible Chrome
Headless Chrome
```

Visible mode is useful for normal operational use because the user can see the ITMIS session.

Headless mode can be useful for unattended automation where a visible browser is not required.

---

# 28. Chrome Profile Handling

The program creates an application-specific Chrome profile under:

```text
%LOCALAPPDATA%\ITMIS_Ticket_Scraper\
```

It can retry Chrome startup using a temporary profile if a profile lock or browser renderer issue is encountered.

This is particularly useful when Chrome is already running or when an earlier Selenium session left profile resources locked.

---

# 29. Application Logging

The program maintains detailed runtime logs in the GUI.

For Live Monitor sessions it can also create a debug log such as:

```text
debug_log_YYYYMMDD_HHMMSS.txt
```

The debug log is initialized when Live Monitor starts.

This is useful for troubleshooting browser automation, ticket detection, extraction, and monitoring problems. fileciteturn2file4L405-L423

---

# 30. Technology Stack

## Programming Language

```text
Python
```

## Desktop GUI

```text
PyQt6
```

## Browser Automation

```text
Selenium
Selenium Manager
webdriver-manager
Google Chrome
ChromeDriver
```

## Data Processing

```text
Pandas
```

## Excel Processing

Pandas uses an Excel engine for reading/writing `.xlsx` files.

## Image Handling

The application optionally uses:

```text
Pillow
```

for image processing.

## System Utilities

```text
psutil
```

is used for process inspection and manual Chrome/ChromeDriver cleanup.

## Packaging

```text
PyInstaller
```

is supported through the supplied:

```text
ticket_scraper.spec
```

---

# 31. Expected Python Dependencies

Based on the imports used by the supplied source, the runtime requires packages in the following categories:

```text
PyQt6
pandas
openpyxl
psutil
Pillow
selenium
webdriver-manager
```

The authoritative dependency versions should be taken from the project's `requirements.txt` when deploying the application. The dependency list above is derived from the Python source rather than from the contents of the supplied requirements file.

---

# 32. Installation

## Step 1 — Install Python

Install a supported Python 3.x release.

Verify:

```powershell
python --version
```

and:

```powershell
pip --version
```

---

## Step 2 — Open the Project Directory

Example:

```powershell
cd C:\ITMIS_Ticket_Scraper
```

---

## Step 3 — Create a Virtual Environment

Recommended:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.venv\Scripts\activate
```

---

## Step 4 — Install Dependencies

```powershell
pip install -r requirements.txt
```

If the requirements file is incomplete, install the packages required by the source:

```powershell
pip install PyQt6 pandas openpyxl psutil Pillow selenium webdriver-manager
```

---

# 33. Running the Python Version

Start the application with:

```powershell
python ticket_scraper.py
```

The application creates the PyQt6 GUI and enters the normal Qt event loop.

---

# 34. Typical Batch-Scraping Procedure

### Step 1

Start:

```powershell
python ticket_scraper.py
```

### Step 2

Select the Excel workbook containing the ticket numbers or URLs.

### Step 3

Allow the application to validate the file.

### Step 4

Start scraping.

### Step 5

Chrome opens and navigates to the configured ITMIS login page.

### Step 6

Log in manually.

### Step 7

The application waits for successful authentication.

### Step 8

The scraper processes each ticket sequentially.

### Step 9

Ticket information is extracted and analyzed.

### Step 10

Results are written to:

```text
rechecked.xlsx
```

and a timestamped backup/summary is generated.

---

# 35. Typical Live-Monitor Procedure

### Step 1

Launch the application.

### Step 2

Open Live Monitor.

### Step 3

Log in to ITMIS manually.

### Step 4

Leave the monitor running.

### Step 5

The monitor watches the dashboard, notifications, and browser tabs.

### Step 6

When a new ticket is detected, it is opened in a separate tab.

### Step 7

The monitor extracts the ticket details.

### Step 8

The ticket is added to the live feed.

### Step 9

Duplicate ticket IDs are ignored.

### Step 10

The session can be restored after restarting the application.

---

# 36. Output Files

A normal scraping session can generate:

```text
rechecked.xlsx
rechecked_backup_YYYYMMDD_HHMMSS.xlsx
summary_YYYYMMDD_HHMMSS.json
```

Live Monitor troubleshooting can generate:

```text
debug_log_YYYYMMDD_HHMMSS.txt
```

The exact location depends on the application's working directory and runtime environment.

---

# 37. PyInstaller Packaging

The repository includes:

```text
ticket_scraper.spec
```

which indicates that the project is designed to support PyInstaller packaging.

A typical build command is:

```powershell
pyinstaller ticket_scraper.spec
```

The exact final packaging behavior should follow the options and resource declarations contained in the supplied `.spec` file.

For a production build, ensure that the following resources are included:

```text
background.png
logo.png
logo.ico
logos/
manifest.xml
```

as required by the application's packaging configuration.

---

# 38. Operational Architecture

The program can be understood as several logical layers.

```text
┌──────────────────────────────────────────────┐
│              PyQt6 User Interface            │
├──────────────────────────────────────────────┤
│ Configuration │ Batch Scraper │ Live Monitor │
├──────────────────────────────────────────────┤
│       Selenium / Chrome Automation Layer      │
├──────────────────────────────────────────────┤
│          ITMIS Web Application                │
├──────────────────────────────────────────────┤
│ Ticket Extraction / Validation / Analysis     │
├──────────────────────────────────────────────┤
│       Pandas / Excel / JSON Output            │
└──────────────────────────────────────────────┘
```

The architecture separates GUI operations from long-running browser work through Qt worker threads. The batch scraper uses `ScraperThread`, while Live Monitor uses `LiveMonitorThread`. This keeps browser automation from blocking the main GUI event loop.

---

# 39. Performance and Reliability Features

The source contains several mechanisms specifically intended to improve reliability:

- Selenium Manager first, webdriver-manager fallback
- system PATH ChromeDriver fallback
- dedicated writable Selenium/Chrome cache
- dedicated Chrome profile
- temporary-profile retry
- login retries
- ticket retries
- session-expiration detection
- browser-state cleanup
- alert handling
- XPath fallback
- partial-success records
- failed-ticket records
- timestamped result backups
- persistent Live Monitor sessions
- duplicate detection
- separate Live Monitor thread
- manual Chrome cleanup function

These features make the application substantially more robust than a simple Selenium script.

---

# 40. Important Limitations

The application depends heavily on the structure of the ITMIS web interface.

In particular, ticket extraction uses XPath selectors tied to the current page structure. If ITMIS changes its Angular/HTML layout, the XPath selectors may need to be updated.

The application also depends on:

- an accessible ITMIS installation
- valid user authentication
- Google Chrome
- compatible Selenium/ChromeDriver behavior
- correct ticket URL structure
- the expected ITMIS dashboard and notification layout

The two-hour `Closed Within Time` calculation is currently implemented as a fixed threshold rather than a configurable SLA profile. fileciteturn1file5L502-L511

---

# 41. Security and Credential Handling

The source is designed around manual login rather than storing or automatically submitting ITMIS credentials.

Users should avoid putting passwords or authentication tokens into:

- source code
- Excel files
- `requirements.txt`
- configuration files
- debug logs
- screenshots
- Git repositories

Browser session data should also be treated as sensitive.

---

# 42. Recommended Project Hygiene

For source-code distribution, consider adding a `.gitignore` containing:

```text
.venv/
__pycache__/
*.pyc
build/
dist/
*.log
debug_log_*.txt
rechecked.xlsx
rechecked_backup_*.xlsx
summary_*.json
*.spec.bak
```

Do not commit generated ticket datasets or debug logs if they contain operationally sensitive information.

---

# 43. Recommended Production Improvements

The current program is already a substantial automation tool. For a future production release, the following improvements would be valuable:

### 43.1 Replace absolute XPath selectors

Prefer stable selectors based on:

```text
id
name
class
data attributes
semantic selectors
```

where available.

This would reduce maintenance when the ITMIS frontend changes.

### 43.2 Externalize SLA Rules

Instead of the current fixed two-hour closure test, allow configuration such as:

```text
Critical
High
Non-Critical
Low
```

with configurable SLA thresholds.

### 43.3 Add Database Storage

A database such as SQLite or MySQL could provide:

- historical ticket storage
- duplicate prevention
- trend analysis
- monthly statistics
- station-level reporting
- equipment-level reporting
- audit history

### 43.4 Add Advanced Analytics

The extracted data could support:

- station fault frequency
- category frequency
- keyword trends
- average resolution time
- SLA compliance
- repeat-fault detection
- daily/weekly/monthly ticket trends

### 43.5 Add Structured Logging

A standard Python `logging` implementation would make production troubleshooting easier than relying primarily on GUI logs and text debug files.

---

# 44. Intended Use

This application is particularly suited to operational teams that need to process a large number of ITMIS service tickets and convert browser-based ticket information into structured maintenance data.

Typical uses include:

- bulk ticket review
- ticket verification
- keyword-based fault identification
- SLA/closure-time checking
- station-wise ticket analysis
- ticket-history extraction
- live ticket detection
- duplicate monitoring
- operational reporting
- maintenance data preparation

---

# 45. Project Summary

**ITMIS Ticket Scraper** is more than a basic web scraper. It combines:

```text
Desktop GUI
+
Browser Automation
+
Ticket Validation
+
Structured Web Extraction
+
Keyword Intelligence
+
Time-Based Analysis
+
Excel Reporting
+
Live Dashboard Monitoring
+
Notification Monitoring
+
Duplicate Detection
+
Session Persistence
```

The batch-processing engine is optimized for turning an Excel list of ITMIS tickets into a structured verification/reporting workbook. The Live Monitor extends the application into an operational surveillance tool capable of detecting newly appearing ITMIS tickets and automatically opening them for detailed extraction.

The supplied source identifies the application as **ITMIS Ticket Scraper v2.0** and implements both batch and continuous monitoring workflows. fileciteturn2file1L129-L165

---

## 46. Author / Ownership

The supplied source does not contain a definitive author/organization attribution beyond the application organization name:

```text
TicketScraper
```

Therefore, no specific author name is asserted in this README.

---

## 47. License

No license declaration was found in the supplied source material.

If this project is intended for GitHub or external distribution, add an explicit license such as:

```text
MIT
Apache-2.0
GPL-3.0
Proprietary / Internal Use Only
```

according to the actual ownership and distribution policy.

---

## 48. Final Description

### Short Description

**ITMIS Ticket Scraper is a PyQt6-based desktop automation application that uses Selenium and Chrome to extract, analyze, validate, and report ITMIS service-ticket information from Excel input lists, while also providing a real-time monitoring system for newly detected dashboard and notification tickets.**

### Detailed Description

ITMIS Ticket Scraper is an operational ticket-management automation platform built in Python. It provides a graphical interface for importing ITMIS ticket numbers or URLs, validating ticket formats, authenticating through the ITMIS web application, extracting ticket descriptions, comments, station information, category, resolution timing, start/resolution timestamps, and last-comment information, and analyzing ticket content against configurable keywords.

The application automates repetitive browser-based ticket verification through Selenium and Chrome while maintaining retry mechanisms, session recovery, browser-profile management, XPath fallbacks, error handling, progress reporting, and structured result generation. Extracted records are exported to Excel together with timestamped backups and JSON summary statistics.

Its Live Monitor subsystem extends the application beyond batch processing. It continuously patrols the ITMIS browser environment, dashboard, and notification bell to identify newly appearing tickets. Detected tickets are checked for duplicates, opened in dedicated browser tabs, and re-extracted from their full ticket pages so that preliminary dashboard or notification information can be upgraded into complete records. Live monitoring statistics, ticket feeds, duplicate counts, and session state are maintained in the GUI, and previous monitoring sessions can be restored after application restart.

The combination of **desktop GUI automation, Selenium-based web extraction, ticket validation, keyword analysis, time-based evaluation, Excel reporting, dashboard monitoring, notification monitoring, duplicate detection, and session persistence** makes the program suitable for operational ITMIS ticket review, maintenance reporting, fault analysis, and continuous ticket surveillance.
