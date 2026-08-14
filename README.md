# ITMIS Ticket Scraper

ITMIS Ticket Scraper 2.0 is a Python-based desktop application for automating ITMIS ticket management and monitoring. It uses PyQt6, Selenium, Chrome, and Pandas to extract ticket information, analyze keywords, check ticket closure timing, and export structured results to Excel.

The application also provides real-time ticket monitoring, detecting newly generated tickets from the ITMIS dashboard and notification system, opening them automatically, and extracting their details.

README.md
# ITMIS Ticket Scraper 2.0


ITMIS Ticket Scraper 2.0 is a Python-based desktop application designed to automate ITMIS ticket extraction, analysis, reporting, and real-time monitoring.


## Features


- ITMIS ticket scraping using Selenium and Chrome
- Excel-based ticket input
- Automatic ticket validation
- Ticket description and comment extraction
- Station and ticket category extraction
- Resolution and ticket timing analysis
- Configurable keyword detection
- Excel result generation
- Real-time ITMIS ticket monitoring
- Dashboard and notification monitoring
- Duplicate ticket detection
- Automatic opening of newly detected tickets
- Retry and error-handling mechanisms
- PyQt6 graphical user interface
- PyInstaller support for Windows executable builds


## Project Structure


```text
ITMIS Ticket Scraper 2.0
│
├── ticket_scraper.py       # Main application
├── requirements.txt        # Python dependencies
├── ticket_scraper.spec     # PyInstaller configuration
├── manifest.xml            # Windows application manifest
├── background.png          # Application background
├── logo.png                # Application logo
└── logo.ico                # Windows application icon
Technologies
Python
PyQt6
Selenium
Google Chrome
Pandas
OpenPyXL
PyInstaller
Installation

Install the required dependencies:

pip install -r requirements.txt

Run the application:

python ticket_scraper.py
Usage
Launch the application.
Configure the ITMIS URLs and scraper settings.
Select the Excel file containing ticket numbers or URLs.
Log in to ITMIS when prompted.
Start the scraper or Live Monitor.
Review the extracted and analyzed ticket information.
Export the results to Excel.
Build Windows EXE

The project includes a PyInstaller specification file:

pyinstaller ticket_scraper.spec

The generated application can be distributed as a Windows executable.

License

No open-source license has currently been specified for this project.

Author

Hunain Zaidi (Hunainzaidi5)