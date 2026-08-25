# ☁️ VINX CLOUD

> **Your Personal Cloud Storage Inside Telegram.**

VINX CLOUD is a powerful Telegram-based cloud storage bot built with **Python**. Upload, organize, manage, search, share, and download your files directly from Telegram.

Files are managed through the VINX CLOUD system, with a private backend storage channel and SQLite-powered metadata management.

---

## ✨ Features

* ☁️ **1 TB Logical Free Storage**
* 📤 Upload Photos, Videos, Documents & More
* 💻 Store Source Code Files
* 📁 Google Drive–Style File Management
* 🔍 File Search
* ⭐ Favorites
* 🗑 Trash & Restore System
* 🔗 Secure File Sharing
* 📊 Dynamic Storage Dashboard
* 📈 Animated Storage Progress Bar
* 📂 Automatic File Categories
* 🕐 Recent Files
* 👤 User Accounts & Storage Statistics
* 💎 Storage Upgrade Plans
* 📢 Force Join System
* 👑 Complete Admin Panel
* 👥 User Management
* 🚫 Ban / Unban System
* 📢 Broadcast System
* 📈 Analytics & Statistics
* 🛠 Maintenance Mode
* 💾 SQLite Database
* 🔐 Secure File Ownership Validation
* ⚡ Rate Limiting
* 🎨 Premium VINX CLOUD Interface

---

## 🖼️ How It Works

```text
User
  │
  ▼
☁️ VINX CLOUD BOT
  │
  ├── 📤 Upload File
  │
  ├── 💾 Storage Check
  │
  ├── 📊 SQLite Metadata
  │
  ▼
☁️ VINX CLOUD SERVER
```

The backend storage system is hidden from normal users.

Users interact only with the **VINX CLOUD** interface.

---

## 🚀 User Flow

```text
/start
   │
   ▼
📢 Force Join
   │
   ▼
✅ Verify
   │
   ▼
☁️ VINX CLOUD Dashboard
   │
   ├── 📤 Upload
   ├── 📁 My Files
   ├── 💾 Storage
   ├── 🔍 Search
   ├── ⭐ Favorites
   ├── 🗑 Trash
   └── 👤 Account
```

---

## 💾 Storage System

Each user receives a configurable **logical storage quota**.

### Default Plan

| Plan    | Storage |
| ------- | ------: |
| 🆓 FREE |    1 TB |
| 💎 PRO  |    2 TB |
| ⚡ ULTRA |    5 TB |
| 👑 MAX  |   10 TB |

Example:

```text
☁️ VINX CLOUD

████████░░░░░░░░░░░░ 40%

Used: 410 GB
Free: 614 GB
Total: 1 TB

📁 Files: 1,842
```

> ⚠️ **Note:** The 1 TB value is a logical quota managed by the bot. It does not mean Telegram physically allocates a dedicated 1 TB server to each user.

---

## 📂 Supported Files

VINX CLOUD supports Telegram-supported file types, including:

* 📷 Images
* 🎬 Videos
* 📄 Documents
* 🎵 Audio
* 🎙 Voice Files
* 📦 ZIP / RAR Archives
* 📱 APK Files
* 💻 Source Code
* 📝 Text Files
* 📊 JSON / XML / SQL
* 🌐 HTML / CSS / JavaScript
* 🐍 Python
* ☕ Java
* ⚙️ C / C++
* 🐘 PHP
* 🦀 Rust
* 🐹 Go
* And more...

---

## 💻 Technology Stack

* **Python 3.10+**
* **python-telegram-bot**
* **SQLite**
* **Python Standard Library**

Everything runs from a single main file:

```text
bot.py
```

---

## 📦 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/vinx-cloud.git
cd vinx-cloud
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

### Linux / macOS

```bash
source venv/bin/activate
```

### Windows

```bash
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file:

```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN

ADMIN_IDS=123456789

BACKEND_CHANNEL_ID=-1001234567890

SUPPORT_USERNAME=yourusername

DEFAULT_STORAGE=1099511627776

DATABASE_PATH=vinx_cloud.db
```

### 5. Start the Bot

```bash
python bot.py
```

---

## 🤖 Telegram Setup

### Create Your Bot

1. Open Telegram.
2. Search for **@BotFather**.
3. Use `/newbot`.
4. Copy your bot token.
5. Add it to your `.env` file.

### Backend Storage Channel

1. Create a private Telegram channel.
2. Add your bot as an administrator.
3. Give the bot the required permissions to post messages/files.
4. Get the channel ID.
5. Add it to:

```env
BACKEND_CHANNEL_ID=-1001234567890
```

This backend channel should remain private.

---

## 📢 Force Join

VINX CLOUD can require users to join specific Telegram channels before accessing the bot.

### Flow

```text
User starts bot
      │
      ▼
📢 Join Required Channels
      │
      ▼
[📢 Join Channel]
      │
      ▼
[✅ Verify]
      │
      ▼
☁️ Access VINX CLOUD
```

Administrators can:

* ➕ Add Channels
* ➖ Remove Channels
* 🟢 Enable Force Join
* 🔴 Disable Force Join

---

## 📁 File Management

Users can:

* 📤 Upload Files
* ⬇️ Download Files
* ✏️ Rename Files
* ⭐ Add to Favorites
* 🗑 Move Files to Trash
* ♻️ Restore Files
* ❌ Permanently Delete Files
* 🔍 Search Files
* 🔗 Generate Secure Share Links

---

## 🔐 Security

VINX CLOUD includes several security protections:

* 🔒 User file ownership verification
* 👑 Admin-only controls
* 🛡 Callback validation
* 🔑 Secure share tokens
* 💉 SQLite parameterized queries
* 🚫 Cross-user file access prevention
* ⚡ Rate limiting
* 🔐 Environment-based credentials

Every file request verifies ownership before allowing access.

```python
file.owner_id == current_user.id
```

Users cannot access files belonging to other users.

---

## 👑 Admin Panel

Administrators can access:

```text
👑 VINX CLOUD ADMIN

📊 Dashboard
👥 Users
💾 Storage
📁 Files
📢 Broadcast
🔐 Force Join
💎 Plans
⚙️ Settings
📈 Analytics
🛠 Maintenance
🚫 Banned Users
```

---

## 📊 Admin Analytics

Track:

* 👥 Total Users
* 🟢 Active Users
* 📁 Total Files
* 💾 Logical Storage Usage
* 📤 Upload Statistics
* ⬇️ Download Statistics
* 📈 Daily Growth
* 💎 Premium Users
* 📷 File Categories

Example:

```text
📈 TODAY

New Users: 128
Uploads: 4,821
Downloads: 2,194
Storage Added: 78 GB
```

---

## 💎 Storage Plans

Administrators can create custom storage plans.

Example:

```text
🆓 FREE
1 TB

💎 PRO
2 TB

⚡ ULTRA
5 TB

👑 MAX
10 TB
```

Payment processing can be integrated separately.

---

## 🔗 Secure File Sharing

Users can create secure sharing links.

Example:

```text
https://t.me/YOUR_BOT?start=share_RANDOM_TOKEN
```

Share links can support expiration:

* ⏱ 1 Hour
* 📅 1 Day
* 📆 7 Days
* ♾ Never

The backend storage channel is never exposed.

---

## 🗑 Trash System

Deleted files are moved to Trash first.

Users can:

```text
♻️ Restore
❌ Permanently Delete
🗑 Empty Trash
```

Restored files are checked against the user's available storage quota.

---

## 🧪 Testing Checklist

* [ ] New user registration
* [ ] Existing user login
* [ ] Force join verification
* [ ] Photo upload
* [ ] Video upload
* [ ] Document upload
* [ ] Source code upload
* [ ] Storage calculation
* [ ] Storage limit check
* [ ] File pagination
* [ ] File search
* [ ] Download
* [ ] Rename
* [ ] Favorites
* [ ] Trash
* [ ] Restore
* [ ] Permanent deletion
* [ ] Secure sharing
* [ ] Admin authentication
* [ ] User management
* [ ] Storage quota modification
* [ ] Broadcast
* [ ] Maintenance mode
* [ ] Invalid callback protection
* [ ] Unauthorized access protection

---

## 🛠️ Troubleshooting

### Bot Doesn't Start

Check:

```text
BOT_TOKEN
```

Make sure the token is valid.

---

### Backend Upload Fails

Check:

* The backend channel ID is correct.
* The bot is added to the channel.
* The bot has administrator permissions.

---

### Force Join Verification Fails

Check:

* The bot can access the required channel.
* The bot has permission to check membership where required.
* The channel configuration is correct.

---

### Database Error

Delete the database only if you understand that this may remove stored metadata:

```text
vinx_cloud.db
```

Restart the bot to automatically initialize a new database.

---

## ⚠️ Important Disclaimer

VINX CLOUD uses a **logical storage quota system**.

For example:

```text
1 TB per user
```

means the bot tracks up to 1 TB of logical storage usage for that user.

Actual file handling and limits depend on Telegram's current platform capabilities, Bot API limits, account restrictions, and applicable policies.

VINX CLOUD should not claim that Telegram provides a dedicated physical 1 TB cloud server to every user.

---

## 🗂️ Project Structure

```text
vinx-cloud/
│
├── bot.py
├── requirements.txt
├── .env
├── .gitignore
├── README.md
└── vinx_cloud.db
```

The complete application logic is designed to run primarily from:

```text
bot.py
```

---

## 📜 License

MIT License

Copyright (c) 2026 VINX

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 🌟 VINX CLOUD

**Your Files. Your Cloud. Your Control.**

☁️ **VINX CLOUD** — Personal cloud storage, powered through Telegram.

Made with ❤️ by **VINX**
