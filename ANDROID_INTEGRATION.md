# WhatsApp Sticker Pack Companion Android App Integration Guide

This document specifies the companion Android app integration design, manifest JSON schema, and Kotlin code implementations required to import converted Telegram sticker packs directly into WhatsApp using WhatsApp's official Sticker API.

---

## 1. Architectural Overview

WhatsApp does not allow direct remote file downloads directly into its sticker database without an installed Android app on the user's device. To bridge Telegram sticker packs to WhatsApp:

1. **Telegram Bot (`/towhatsapp`)**:
   - Converts Telegram stickers (static or `.tgs` animated) to WhatsApp-compliant 512x512 WebP files (Static < 100 KB, Animated < 500 KB).
   - Generates 96x96 WebP tray icons (< 50 KB).
   - Partitions stickers into valid WhatsApp chunks (between 3 and 30 stickers per pack).
   - Bundles each chunk into a `.zip` archive containing `contents.json`, `tray_icon.webp`, and `01.webp`, `02.webp`, ...
   - Emits a structured `manifest.json` metadata file.

2. **Android Companion App**:
   - Receives or reads the converted `.zip` pack chunks and `manifest.json`.
   - Exposes sticker assets locally via a custom `ContentProvider`.
   - Fires the official `com.whatsapp.intent.action.ENABLE_STICKER_PACK` intent to prompt WhatsApp to import the sticker pack.

---

## 2. Manifest JSON Payload Schema

When a user runs `/towhatsapp`, the bot generates a `manifest.json` file detailing the pack chunks:

```json
{
  "title": "Anime Cute Stickers",
  "publisher": "Yuki Bot",
  "total_stickers": 35,
  "total_chunks": 2,
  "chunks": [
    {
      "chunk_index": 1,
      "pack_identifier": "anime_cute_stickers_static_part_1",
      "title": "Anime Cute Stickers (Static Part 1)",
      "is_animated": false,
      "sticker_count": 30,
      "zip_filename": "anime_cute_stickers_static_part_1.zip",
      "zip_size_bytes": 1245000
    },
    {
      "chunk_index": 2,
      "pack_identifier": "anime_cute_stickers_static_part_2",
      "title": "Anime Cute Stickers (Static Part 2)",
      "is_animated": false,
      "sticker_count": 5,
      "zip_filename": "anime_cute_stickers_static_part_2.zip",
      "zip_size_bytes": 210000
    }
  ],
  "android_integration": {
    "intent_action": "com.whatsapp.intent.action.ENABLE_STICKER_PACK",
    "required_app": "Companion Android App required for direct WhatsApp import.",
    "instructions": "Install the companion Android app, grant storage permissions, and trigger ENABLE_STICKER_PACK intent with authority."
  }
}
```

---

## 3. WhatsApp Zip Chunk Structure (`contents.json`)

Inside each `.zip` bundle created by the bot:
- `contents.json`
- `tray_icon.webp` (96x96, < 50 KB)
- `01.webp`, `02.webp`, ... (512x512, Static < 100 KB, Animated < 500 KB)

Example `contents.json`:
```json
{
  "android_play_store_link": "",
  "ios_app_store_link": "",
  "sticker_packs": [
    {
      "identifier": "anime_cute_stickers_static_part_1",
      "name": "Anime Cute Stickers (Static Part 1)",
      "publisher": "Yuki Bot",
      "tray_image_file": "tray_icon.webp",
      "publisher_email": "",
      "publisher_website": "",
      "privacy_policy_website": "",
      "license_agreement_website": "",
      "animated_sticker_pack": false,
      "stickers": [
        {
          "image_file": "01.webp",
          "emojis": ["🌸"]
        },
        {
          "image_file": "02.webp",
          "emojis": ["💕"]
        }
      ]
    }
  ]
}
```

---

## 4. Kotlin Android Companion App Stubs

### 4.1 `AndroidManifest.xml`
Declare WhatsApp intent visibility queries and your `StickerContentProvider`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.whatsappstickers">

    <!-- Allow querying WhatsApp package for intent handling -->
    <queries>
        <package android:name="com.whatsapp" />
        <package android:name="com.whatsapp.w4b" />
    </queries>

    <application
        android:allowBackup="true"
        android:label="WhatsApp Sticker Companion"
        android:supportsRtl="true"
        android:theme="@style/Theme.AppCompat.Light.DarkActionBar">

        <activity
            android:name=".AddStickerPackActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <provider
            android:name=".StickerContentProvider"
            android:authorities="${applicationId}.stickercontentprovider"
            android:enabled="true"
            android:exported="true"
            android:readPermission="com.whatsapp.sticker.READ_PERMISSION" />

    </application>
</manifest>
```

---

### 4.2 `StickerContentProvider.kt`
Implementation of the Android `ContentProvider` queried by WhatsApp:

```kotlin
package com.example.whatsappstickers

import android.content.ContentProvider
import android.content.ContentValues
import android.content.UriMatcher
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileNotFoundException

class StickerContentProvider : ContentProvider() {

    companion object {
        const val AUTHORITY = "com.example.whatsappstickers.stickercontentprovider"
        const val STICKER_PACKS_PATH = "sticker_packs"
        const val STICKERS_PATH = "stickers"
        const val STICKERS_ASSET_PATH = "stickers_asset"

        private const val CODE_STICKER_PACKS = 1
        private const val CODE_STICKER_PACKS_WITH_ID = 2
        private const val CODE_STICKERS = 3
        private const val CODE_STICKER_ASSET = 4

        private val MATCHER = UriMatcher(UriMatcher.NO_MATCH).apply {
            addURI(AUTHORITY, STICKER_PACKS_PATH, CODE_STICKER_PACKS)
            addURI(AUTHORITY, "$STICKER_PACKS_PATH/*", CODE_STICKER_PACKS_WITH_ID)
            addURI(AUTHORITY, "$STICKERS_PATH/*", CODE_STICKERS)
            addURI(AUTHORITY, "$STICKERS_ASSET_PATH/*/*", CODE_STICKER_ASSET)
        }
    }

    override fun onCreate(): Boolean {
        return true
    }

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?
    ): Cursor? {
        val code = MATCHER.match(uri)
        return when (code) {
            CODE_STICKER_PACKS -> getPackAllCursor()
            CODE_STICKER_PACKS_WITH_ID -> getPackCursor(uri.lastPathSegment ?: "")
            CODE_STICKERS -> getStickersCursor(uri.lastPathSegment ?: "")
            else -> throw IllegalArgumentException("Unknown URI: $uri")
        }
    }

    private fun getPackAllCursor(): Cursor {
        val cursor = MatrixCursor(
            arrayOf(
                "sticker_pack_identifier",
                "sticker_pack_name",
                "sticker_pack_publisher",
                "sticker_pack_icon",
                "android_play_store_link",
                "ios_app_store_link",
                "publisher_email",
                "publisher_website",
                "privacy_policy_website",
                "license_agreement_website",
                "animated_sticker_pack"
            )
        )
        // Read unzipped pack directories inside context.filesDir / "sticker_packs"
        val packsDir = File(context?.filesDir, "sticker_packs")
        if (packsDir.exists()) {
            packsDir.listFiles()?.filter { it.isDirectory }?.forEach { packDir ->
                val contentsFile = File(packDir, "contents.json")
                if (contentsFile.exists()) {
                    // Populate cursor row from contents.json
                    cursor.addRow(
                        arrayOf(
                            packDir.name,
                            packDir.name.replace("_", " ").capitalize(),
                            "Yuki Bot",
                            "tray_icon.webp",
                            "", "", "", "", "", "", 0
                        )
                    )
                }
            }
        }
        return cursor
    }

    private fun getPackCursor(packId: String): Cursor {
        return getPackAllCursor() // Filter by packId in production
    }

    private fun getStickersCursor(packId: String): Cursor {
        val cursor = MatrixCursor(arrayOf("sticker_file_name", "sticker_emoji"))
        val packDir = File(File(context?.filesDir, "sticker_packs"), packId)
        if (packDir.exists()) {
            packDir.listFiles()?.filter { it.name.endsWith(".webp") && it.name != "tray_icon.webp" }
                ?.forEach { stickerFile ->
                    cursor.addRow(arrayOf(stickerFile.name, "🌸"))
                }
        }
        return cursor
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val match = MATCHER.match(uri)
        if (match == CODE_STICKER_ASSET) {
            val pathSegments = uri.pathSegments
            val packId = pathSegments[1]
            val fileName = pathSegments[2]
            val file = File(File(File(context?.filesDir, "sticker_packs"), packId), fileName)
            if (file.exists()) {
                return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
            }
        }
        throw FileNotFoundException("File not found for URI: $uri")
    }

    override fun getType(uri: Uri): String? = when (MATCHER.match(uri)) {
        CODE_STICKER_PACKS -> "vnd.android.cursor.dir/vnd.$AUTHORITY.$STICKER_PACKS_PATH"
        CODE_STICKER_PACKS_WITH_ID -> "vnd.android.cursor.item/vnd.$AUTHORITY.$STICKER_PACKS_PATH"
        CODE_STICKERS -> "vnd.android.cursor.dir/vnd.$AUTHORITY.$STICKERS_PATH"
        CODE_STICKER_ASSET -> "image/webp"
        else -> null
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
}
```

---

### 4.3 `AddStickerPackActivity.kt`
Triggers direct WhatsApp sticker pack addition dialog:

```kotlin
package com.example.whatsappstickers

import android.content.ActivityNotFoundException
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class AddStickerPackActivity : AppCompatActivity() {

    companion object {
        const val ADD_PACK_REQUEST_CODE = 200
        const val EXTRA_STICKER_PACK_ID = "sticker_pack_id"
        const val EXTRA_STICKER_PACK_AUTHORITY = "sticker_pack_authority"
        const val EXTRA_STICKER_PACK_NAME = "sticker_pack_name"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val packId = intent.getStringExtra(EXTRA_STICKER_PACK_ID) ?: "anime_cute_stickers_static_part_1"
        val packName = intent.getStringExtra(EXTRA_STICKER_PACK_NAME) ?: "Anime Cute Stickers"

        addStickerPackToWhatsApp(packId, packName)
    }

    private fun addStickerPackToWhatsApp(packId: String, packName: String) {
        val intent = Intent("com.whatsapp.intent.action.ENABLE_STICKER_PACK").apply {
            putExtra(EXTRA_STICKER_PACK_ID, packId)
            putExtra(EXTRA_STICKER_PACK_AUTHORITY, StickerContentProvider.AUTHORITY)
            putExtra(EXTRA_STICKER_PACK_NAME, packName)
        }

        try {
            startActivityForResult(intent, ADD_PACK_REQUEST_CODE)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "WhatsApp is not installed on this device!", Toast.LENGTH_LONG).show()
            finish()
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == ADD_PACK_REQUEST_CODE) {
            if (resultCode == RESULT_OK) {
                Toast.makeText(this, "Sticker pack added to WhatsApp successfully! 🎉", Toast.LENGTH_SHORT).show()
            } else {
                val validationError = data?.getStringExtra("validation_error")
                Toast.makeText(this, "Failed to add pack: ${validationError ?: "Cancelled"}", Toast.LENGTH_LONG).show()
            }
            finish()
        }
    }
}
```
