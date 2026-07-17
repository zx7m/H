# YouTube Stream URL Extraction - Research Document

## Overview

This document describes how YouTube's watch page delivers video stream data, how
`ytInitialPlayerResponse` is embedded, how player JS URLs are obtained, how stream
URLs are encoded in `formats`/`adaptiveFormats`, how the `n`-parameter throttling
works, and how the signature cipher operates. This research directly guides the
implementation of the native stream resolver, n-resolver, and cipher modules.

---

## 1. How ytInitialPlayerResponse Is Embedded in Page HTML

### Location in HTML

YouTube embeds the initial player response as a **top-level JavaScript variable**
assignment in the raw HTML of the watch page:

```html
<script>var ytInitialPlayerResponse = { ... }; </script>
```

It appears early in the page, typically within a `<script>` tag near the top of
`<body>`. It is NOT inside a function or an IIFE — it is a direct global variable
assignment, making it trivially extractable with a regex.

### Variants of the Assignment Pattern

YouTube has used multiple assignment syntaxes across clients and page types:

| Variant | Example |
|---|---|
| `var` declaration | `var ytInitialPlayerResponse = {...};` |
| `var` with semicolon after JSON | `var ytInitialPlayerResponse = {...};` |
| `window["..."]` assignment | `window["ytInitialPlayerResponse"] = {...};` |
| Shorts / Embed pages | Same as watch page, though data may be minimal |

The regex in `metadata.py` currently handles the `var` form:
```python
INITIAL_PLAYER_RESPONSE_PATTERN = re.compile(
    r"var\s+ytInitialPlayerResponse\s*=\s*(\{)",
    re.DOTALL,
)
```

A more robust pattern that also captures `window["ytInitialPlayerResponse"] = {...}`
should be used in the final implementation.

### Extraction Strategy (as implemented in metadata.py)

`metadata.py:_extract_json_object` uses a **hand-rolled brace-depth parser**
to extract the full JSON object starting from the first `{` matched by the regex.
This correctly handles:
- Nested objects (e.g. `streamingData`, `videoDetails`, `playabilityStatus`)
- Strings containing `{` or `}` characters (escape-aware, string-aware)
- Escaped Unicode characters (`\uXXXX`)

The result is parsed with `json.loads()` into a Python `dict`.

---

## 2. Exact Data Structure of ytInitialPlayerResponse

### Top-Level Keys

```python
{
    "videoDetails": { ... },
    "playabilityStatus": { ... },
    "streamingData": { ... },
    "microformat": { ... },
    # Other keys present but not needed for downloading:
    # "adPlacements", "adBreaks", "annotations", "cards", "endscreen", etc.
}
```

### `videoDetails` Fields

```python
{
    "videoId": "dQw4w9WgXcQ",           # str, 11-char ID
    "title": "Never Gonna Give You Up",  # str
    "author": "Rick Astley",             # str
    "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",  # str
    "lengthSeconds": "212",              # str, numeric string
    "viewCount": "1400000000",           # str, numeric string
    "keywords": ["pop", "music", ...],   # list[str]
    "shortDescription": "...",           # str
    "thumbnail": {                        # dict
        "thumbnails": [
            {"url": "...", "width": 120, "height": 90},
            # ...more sizes
        ]
    },
    "isLiveContent": False,              # bool
    "isPrivate": False,                  # bool
    "isLiveDvrEnabled": False,           # bool
    "isLowLatencyLiveDvrEnabled": False, # bool
    "isCrawlable": True,                 # bool
    "allowRatings": True,                # bool
    "averageRating": 4.5,                # float
    "isFamilySafe": True,                # bool
    "isUnpluggedCorpus": False,          # bool
}
```

### `playabilityStatus` Fields

```python
{
    "status": "OK",                        # str: OK, LOGIN_REQUIRED, UNPLAYABLE,
                                           #      AGE_CHECK_REQUIRED, ERROR, etc.
    "playableInEmbed": True,               # bool
    "contextParams": "...",                # str (optional)
    "reason": "...",                       # str (only on error statuses)
    "miniplayer": { ... },                 # dict (only on OK)
    "miniplayerIconUrl": "...",            # str (only on error statuses)
}
```

Valid status values:
- `OK` — video is playable
- `LOGIN_REQUIRED` — age-restricted or private; requires login
- `UNPLAYABLE` — video cannot be played (embedding disabled, etc.)
- `AGE_CHECK_REQUIRED` — age gate must be passed
- `AGE_CHECK_NOT_ALLOWED` — age-restricted, not allowed
- `ERROR` — generic playback error
- `AGE_RESTRICTED` — age-restricted content
- `LIVE_STREAM_OFFLINE` — live stream not yet started
- `LIVE_STREAM_OFFLINE_WITH_CONTENT` — live stream with VOD content

### `streamingData` Fields (the most important for downloading)

```python
{
    "expiresInSeconds": "21540",   # str: URL TTL in seconds (~6 hours)
    "formats": [ ... ],             # list[dict]: progressive (audio+video combined) formats
    "adaptiveFormats": [ ... ],     # list[dict]: separate audio-only and video-only formats
    # Optional (present for some videos):
    "dashManifestUrl": "...",       # str: DASH MPD manifest URL
    "hlsManifestUrl": "...",        # str: HLS manifest URL
}
```

### Format Object Fields (both `formats` and `adaptiveFormats`)

```python
{
    # --- Identity ---
    "itag": 18,                          # int: YouTube's internal format ID

    # --- URL / Cipher ---
    "url": "https://rr12---sn-3c27sn7d.googlevideo.com/videoplayback?...",
                                       # str: direct stream URL (may be absent if encrypted)
    "signatureCipher": "url=...&s=...&sp=...",
                                       # str: URL-encoded cipher dict (present instead of `url`
                                       #       when stream is signature-protected)

    # --- MIME / Codec ---
    "mimeType": "video/mp4; codecs=\"avc1.42001E, mp4a.40.2\"",
                                       # str: MIME type with codec string
    "type": "video",                     # str: "video" or "audio" (legacy, not always present)

    # --- Video properties (may be absent for audio-only) ---
    "width": 640,                        # int
    "height": 360,                       # int
    "fps": 30,                           # int: frames per second
    "quality": "medium",                 # str: quality label (small, medium, hd, hd720, etc.)
    "qualityLabel": "360p",              # str: human-readable quality (e.g. "1080p", "720p")

    # --- Audio properties (may be absent for video-only) ---
    "audioQuality": "AUDIO_QUALITY_LOW", # str: AUDIO_QUALITY_LOW, _MEDIUM, _HIGH
    "audioSampleRate": 22050,            # int: Hz
    "audioChannels": 2,                  # int: 1 (mono) or 2 (stereo)

    # --- Bitrate ---
    "bitrate": 503351,                   # int: overall bitrate in bits per second
    "approxDurationMs": "183994",        # str: duration in milliseconds
    "contentLength": "20971520",         # str: file size in bytes (may be absent)
    "lastModified": "1665725827618480",  # str: epoch milliseconds as string

    # --- Container / Protocol ---
    "projectionType": "RECTANGULAR",     # str: RECTANGULAR, EQUIRECTANGULAR, CUBEMAP
    # `protocol` is NOT a top-level field in ytInitialPlayerResponse formats;
    # it is inferred from the URL scheme or the mimeType.
}
```

### Key distinctions between `formats` and `adaptiveFormats`

| Property | `formats` | `adaptiveFormats` |
|---|---|---|
| Contains | Progressive (audio+video muxed) | Separate audio-only and video-only streams |
| Audio | Both audio and video present | Either audio-only or video-only |
| DASH | Non-DASH progressive HTTP streams | DASH and HLS adaptive streams |
| n-parameter | May or may not have `n` param | May or may not have `n` param |
| Signature cipher | May have `signatureCipher` | May have `signatureCipher` |

### Decoding `mimeType` into (container, vcodec, acodec)

The `mimeType` field follows the standard format:
```
type/subtype; codecs="vcodec[.profile], acodec[.profile]"
```

Examples:
- `"video/mp4; codecs=\"avc1.42001E, mp4a.40.2\""` → container=`mp4`, vcodec=`avc1`, acodec=`mp4a`
- `"video/webm; codecs=\"vp9, opus\""` → container=`webm`, vcodec=`vp9`, acodec=`opus`
- `"audio/webm; codecs=\"opus\""` → container=`webm`, vcodec=`none`, acodec=`opus`

The `type` (video/audio) is determined by whether `width`/`height` are present.

---

## 3. How the Player JS URL Is Obtained

There are **two methods** to get the player JS URL. In practice, `metadata.py` does
NOT currently extract the player JS URL — it only gets `streamingData` from
`ytInitialPlayerResponse`. The native implementation will need both sources.

### Method A: From `ytInitialPlayerResponse` → `assets`

Some pages embed the player JS URL inside `ytInitialPlayerResponse`:

```python
player_data["assets"]["js"]   # e.g. "/s/player/5352eb4f/player_ias.vflset/en_US/base.js"
```

Full path:
```
https://www.youtube.com{player_data["assets"]["js"]}
```

### Method B: From `ytcfg` in the page HTML

The `ytcfg` object is another top-level JavaScript variable in the watch page:

```html
<script>var ytcfg = {"INNER_TUBE_API_KEY":"AIzaSyA8...","INNER_TUBE_CLIENT_NAME":"WEB","INNER_TUBE_CLIENT_VERSION":"2.20240118.01.00",...};</script>
```

Within `ytcfg`:
- `ytcfg.set("PLAYER_JS_URL", "/s/player/HASH/player_ias.vflset/en_US/base.js")` may be set programmatically.
- `ytcfg.data` contains configuration including the player JS path.

More reliably, the `ytcfg` object contains:
```python
{
    "INNER_TUBE_API_KEY": "AIzaSy...",
    "INNER_TUBE_CLIENT_NAME": "WEB",
    "INNER_TUBE_CLIENT_VERSION": "2.20240118.01.00",
    # ...other configuration keys
}
```

### Method C: Regex from raw HTML (most reliable)

Search the raw HTML for the player JS script tag:
```python
RE_PLAYER_JS = re.compile(
    r'"js":\s*"([^"]+)"',
    re.DOTALL,
)
```

Or search for the `player_ias.vflset` URL pattern:
```python
re.search(r'/s/player/([a-f0-9]{8})/player_ias\.vflset/[^/]+/base\.js', html)
```

The extracted path is then prefixed with `https://www.youtube.com` to produce the
full URL, e.g.:
```
https://www.youtube.com/s/player/5352eb4f/player_ias.vflset/en_US/base.js
```

### `sts` Token

The `sts` token is a numeric timestamp that identifies the player version used to
generate the stream URLs. It is extracted from the page HTML:
```python
RE_STS = re.compile(r'"sts"\s*:\s*(\d+)')
```

It is sent as a query parameter `sts=<value>` to the `/get_video_info` endpoint
(legacy) or embedded in the `playbackContext.contentPlaybackContext.signatureTimestamp`
field of the `/youtubei/v1/player` POST body (modern API).

---

## 4. How Stream URLs Are Encoded in `formats` / `adaptiveFormats`

### Direct URLs (no cipher)

When the stream URL does not require signature deciphering, the format dict
contains a `url` key with a fully-formed, signed, query-parameter-encoded URL:

```python
{
    "itag": 18,
    "url": "https://rr12---sn-3c27sn7d.googlevideo.com/videoplayback?"
           "expire=1700000000&ei=abc123&ip=1.2.3.4"
           "&id=o-abc123"
           "&itag=18&source=youtube&requiressl=yes"
           "&mime=video%2Fmp4"
           "&cnr=14&ratebypass=yes"
           "&dur=183.994&lmt=1700000000000"
           "&sparams=expire,ei,ip,id,itag,source,requiressl,mime,cnr,ratebypass,dur,lmt"
           "&sig=AOq0QJ8wRQIge8aU9csL5Od685kA1to0PB6ggVeuLJjfSfTpZVsgEToCIQDZEk4..."
           "&lsparams=mh,mm,mn,ms,mv,mvi,pl,initcwndbps"
           "&lsig=AG3C_xAwRgIhAP5rrAq5OoZ0e5bgNZpztkbKGgayb-tAfBbM3Z4VrpDfAiEAkcg...",
    "mimeType": "video/mp4; codecs=\"avc1.42001E, mp4a.40.2\"",
    "itag": 18,
    ...
}
```

**Critical query parameters in the URL:**
| Parameter | Purpose |
|---|---|
| `expire` | Unix timestamp when URL expires (6-hour window typical) |
| `ei` | YouTube session identifier |
| `ip` | Client IP address baked into URL |
| `itag` | Format identifier (must match the format dict's `itag`) |
| `source` | Always `youtube` |
| `requiressl` | `yes` — HTTPS required |
| `mime` | URL-encoded MIME type |
| `sparams` | Comma-separated list of parameters covered by the signature |
| `sig` / `signature` | The signature value (deciphered from `s`) |
| `s` | Encrypted signature (present in `signatureCipher` only) |
| `sp` | The parameter name for the signature (usually `signature`) |
| `n` | Throttle parameter value (deciphered from JS n-function) |
| `lsparams` | Comma-separated list of parameters in `lsig` |
| `lsig` | Secondary signature |

### Cipher URLs (`signatureCipher`)

When a stream URL is encrypted, the `url` key is **absent** and a `signatureCipher`
key is present instead. This is a URL-encoded string that encodes multiple fields:

```
signatureCipher = "url=<base_url>&s=<encrypted_signature>&sp=<param_name>&n=<n_value>"
```

Decoded components:
| Key | Value | Description |
|---|---|---|
| `url` | URL-encoded base URL (no signature params) | The stream URL without the `s`/`sp`/`n` params |
| `s` | Encrypted signature string | The ciphertext that must be deciphered |
| `sp` | `signature` (typically) | The query parameter name for the deciphered value |
| `n` | `A_vI6Ix_3g` (example) | The n-function name (used for throttling) |

After deciphering:
1. Decipher `s` using the player JS signature function → `deciphered_sig`
2. Append `&sp=deciphered_sig` to the base URL
3. Compute `n` value if present and append as `&n=computed_n_value`

---

## 5. The `n`-Parameter (Throttling)

### What It Is

YouTube appends an `n` query parameter to stream URLs to implement **throttling**.
The value of `n` is a short string computed by a JavaScript function in the player
JS. Without the correct `n` value, YouTube returns the stream at reduced bandwidth
or returns a 403/redirect.

### Where It Appears

The `n` parameter appears directly in the stream URL query string:
```
...&n=Ed7FM_&ratebypass=yes&...
```

It is also present in the `signatureCipher` encoded string:
```
...&n=Ed7FM_...
```

### How the n-Function Is Invoked (in player JS)

The n-function is invoked in the player JS within a loop that iterates over
format URLs:

```javascript
// Pattern A (older): n is in array position
a.D && (b = a.get("n")) && (b = FRa[0](b), a.set("n", b), FRa.length || Fma(""))

// Pattern B (2024+): n referenced via String.fromCharCode(110)
a.D && (b = String.fromCharCode(110), c = a.get(b)) && (c = IRa[0](c), a.set(b, c), ...)

// Pattern C (2025+): Q-array obfuscated
// Function name hidden in Q array; n lookup uses array index arithmetic
```

Where:
- `a.D` is a truthy check for format URLs having `n` parameter
- `a.get("n")` retrieves the n-value from the format URL
- `FRa[0](b)` or `IRa[0](c)` calls the n-transform function
- The result is set back as the `n` parameter value

### n-Function Algorithm Patterns

The n-function typically implements a simple character transformation on the input
string. Known patterns include:
- **Reversal**: `str.split("").reverse().join("")`
- **Swap**: Swap character at index 0 with character at computed index
- **Slice**: `str.slice(N)` — drop first N characters
- **Splice**: `str.splice(0, N)` — remove N characters from start

The specific algorithm changes whenever YouTube releases a new player JS version.

### Extraction Strategy

1. Download the player JS from the URL obtained via Method A or B above.
2. Search for the n-function invocation pattern using regex:
   ```python
   N_FUNCTION_PATTERNS = [
       r'\.get\("n"\)\)&&\(b=([a-zA-Z0-9$]+)(?:\[(\d+)\])?\(([a-zA-Z0-9])\)',
       r'\(\s*([a-zA-Z0-9$]+)\s*=\s*String\.fromCharCode\(110\)',
       # Q-array patterns require hardcoded config lookup
   ]
   ```
3. Extract the function body using regex (boundary search from function name to `};`).
4. For simple cases, translate the JS operations directly into Python.
5. For complex cases (Q-array obfuscation, wrapper functions), fall back to a
   **hardcoded config table** keyed by player JS hash.

### Fallback Behavior

If the n-parameter resolver fails, the implementation should:
- Log a warning
- Use the URL without the n-parameter (may result in throttling or 403)
- Optionally retry with alternative user agents

---

## 6. Signature Cipher (Deciphering Encrypted Stream URLs)

### What It Is

When YouTube wants to prevent hotlinking or unauthorized access to stream URLs,
it replaces the `url` field with a `signatureCipher` field. The URL contained
within is encrypted — specifically the **signature** portion of the URL is
obfuscated.

### `signatureCipher` Structure

```
signatureCipher = URL-encode({
    "url": "https://rr12---sn-3c27sn7d.googlevideo.com/videoplayback?expire=...&itag=18&...",
    "s":   "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",   # encrypted signature
    "sp":  "signature",                                  # param name for deciphered sig
})
```

### Deciphering Pipeline

```
1. Parse signatureCipher → {url, s, sp}
2. Download player JS (same URL as n-parameter resolver)
3. Extract the signature decipher function name from player JS
4. Extract the function body
5. Implement the algorithm in Python:
   a. Reverse (if present)
   b. Slice (if present — drop first N chars)
   c. Swap (if present — swap char[0] with char[N])
6. Apply operations in order to `s` → deciphered_sig
7. Append `&{sp}={deciphered_sig}` to the base URL
```

### Signature Function Extraction Patterns

```python
SIG_FUNCTION_PATTERNS = [
    # 2025+ pattern: &&(VAR=FUNC(NUM,decodeURIComponent(VAR))
    r'&&\s*\(\s*[a-zA-Z0-9$]+\s*=\s*([a-zA-Z0-9$]+)\s*\(\s*(\d+)\s*,\s*decodeURIComponent\s*\(\s*[a-zA-Z0-9$]+\s*\)',
    # Older pattern: c=decodeURIComponent(a.get("s")) && (a.set("s", FUNC(c)), ...
    r'\bc\s*&&\s*d\.set\([^,]+,\s*(?:encodeURIComponent\s*\()?\s*([\w$]+)\(',
]
```

### Known Cipher Operations

| Operation | JS | Python Equivalent |
|---|---|---|
| Reverse | `a=a.split("").reverse().join("")` | `s = s[::-1]` |
| Slice | `a=a.slice(N)` | `s = s[N:]` |
| Swap | `a=[a[1],a[0],...a[N],a[0]]` (swap index 0 with N) | `s = s[N] + s[1:N] + s[0] + s[N+1:]` |
| Splice | `a.splice(0, N)` | `s = s[N:]` |

The function is typically invoked as:
```javascript
// 2025+ pattern
&& (decoded_sig = SIGFUNC(CONSTANT_ARG, decodeURIComponent(encrypted_sig)))
// or
c = decodeURIComponent(a.get("s")) && (a.set("s", SIGFUNC(c)), a.url = ...)
```

### Q-Array Obfuscation (2025–2026+)

YouTube increasingly obfuscates function names and arguments using "Q-array"
obfuscation:
```javascript
var Q = "a0:a1:a2:...".split(":");
// Function names are references into Q, e.g. Q[T^6001]
```

When Q-array obfuscation is detected:
1. Parse the Q array to resolve function names
2. Or use a **hardcoded config table** keyed by player JS hash
3. Known configs store: function names, constant arguments, operation order

### `signatureTimestamp`

The `signatureTimestamp` is extracted from player JS and sent as
`playbackContext.contentPlaybackContext.signatureTimestamp` in the API POST body
to `/youtubei/v1/player`. This ensures the returned stream descriptors match the
current player's cipher version.

---

## 7. Complete Stream URL Resolution Pipeline

```
1. GET https://www.youtube.com/watch?v={video_id}
   Headers: User-Agent, Accept, Accept-Language, Referer

2. Parse response HTML:
   a. Extract ytInitialPlayerResponse (var ytInitialPlayerResponse = {...})
   b. Extract ytcfg (var ytcfg = {...})
   c. Extract sts ("sts": <number>)
   d. Extract player JS URL from HTML or ytInitialPlayerResponse.assets.js

3. Parse ytInitialPlayerResponse:
   a. Validate playabilityStatus.status == "OK"
   b. Extract streamingData.formats
   c. Extract streamingData.adaptiveFormats
   d. Extract videoDetails

4. For each format in formats + adaptiveFormats:
   a. If `url` key present → direct URL (no cipher)
   b. If `signatureCipher` key present:
      i.   Parse cipher: url, s, sp, n
      ii.  Download player JS
      iii. Extract n-function and decipher function
      iv.  Compute n-value (if n present in cipher)
      v.   Decipher s → deciphered_signature
      vi.  Append &sp={deciphered_signature} to base URL
      vii. Append &n={n_value} if n was present

5. Select best format:
   a. Filter by preferred container (mp4 > webm)
   b. Filter by preferred video codec (avc1 > vp9 > vp8)
   c. Filter by preferred audio codec (aac > opus > mp3)
   d. For video-only + audio-only pair, merge via ffmpeg

6. Download the resolved stream URL with appropriate headers:
   - User-Agent (browser-like)
   - Referer: https://www.youtube.com/
   - Accept: */*
   - Cookies from initial page fetch (critical for 403 avoidance)
```

---

## 8. Headers Required for Stream URL Access

Stream URLs must be fetched with specific headers to avoid 403:

```python
STREAM_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
    "Origin": "https://www.youtube.com",
}
```

**Cookies from the initial page fetch must be forwarded.** This is critical —
YouTube validates that the requesting client has a valid session cookie matching
the IP baked into the stream URL's `ip` parameter.

---

## 9. URL Expiry and Renewal

Stream URLs expire after `expiresInSeconds` (typically 6 hours = 21600 seconds).
After expiry, a 403 is returned. The solution is to:
- Re-fetch the watch page HTML
- Re-parse `ytInitialPlayerResponse`
- Re-resolve all stream URLs (fresh signatures and n-values)

---

## 10. Key Implementation Notes for This Project

### Current `metadata.py` Strengths
- ✅ Handles `var ytInitialPlayerResponse = {...}` extraction
- ✅ Uses brace-depth parser (handles nested JSON)
- ✅ Handles playability status checks
- ✅ Extracts `streamingData`, `videoDetails`, `microformat`
- ✅ Extracts `formats` and `adaptiveFormats`

### Current `metadata.py` Gaps (to be addressed by new modules)
- ❌ Does NOT extract player JS URL
- ❌ Does NOT extract `ytcfg`
- ❌ Does NOT extract `sts`
- ❌ Does NOT handle `signatureCipher` (only `url` field)
- ❌ Does NOT resolve n-parameter
- ❌ Does NOT decipher signatures
- ❌ Does NOT select best format
- ❌ Regex only handles `var ytInitialPlayerResponse`, not `window["..."]` variant

### `constants.py` Notes
- ✅ Comprehensive itag definitions (ITAG_QUALITY, ITAG_DETAILS)
- ✅ MIME type maps, protocol constants, codec lists
- ✅ Quality level maps
- ✅ Regex patterns already defined (RE_PLAYER_RESPONSE, RE_YTCFG, RE_STS, etc.)
- ⚠️ `FORMAT_BEST_COMBINED`, `FORMAT_BEST_AUDIO`, `FORMAT_BEST_MP4`, `OUTPUT_TEMPLATE`
  are yt-dlp format strings that should be replaced with native equivalents

### New Modules Required

| Module | Purpose |
|---|---|
| `html_extractor.py` | Extract ytInitialPlayerResponse, ytcfg, sts, player JS URL from HTML |
| `player_response.py` | Parse ytInitialPlayerResponse into structured data (videoDetails, streamingData, etc.) |
| `streaming_data.py` | Parse formats/adaptiveFormats into StreamFormat objects; select best format |
| `n_resolver.py` | Download player JS, extract n-function, compute n-value |
| `signature_cipher.py` | Parse signatureCipher, download player JS, extract decipher function, apply to URL |
| `http_downloader.py` | Download stream files with retry, cookies, headers |
| `downloader.py` (rewrite) | Orchestrate: fetch page → parse → resolve URLs → download |

---

## 11. Player JS URL Format

```
https://www.youtube.com/s/player/{HASH}/player_ias.vflset/{LOCALE}/base.js
```

Where:
- `{HASH}` is an 8-character hex string identifying the player version (e.g. `5352eb4f`)
- `{LOCALE}` is the locale code (e.g. `en_US`, `de_DE`)
- The player JS is ~1-3 MB of heavily obfuscated JavaScript

### Player JS Caching

The player JS should be cached by its HASH (the URL path component). If the same
player version is encountered again, the cached copy can be reused. The cache
should be invalidated after a configurable TTL (e.g. 24 hours).

---

## 12. Error Handling Strategy

| Error | Cause | Recovery |
|---|---|---|
| `MetadataExtractionError` | No ytInitialPlayerResponse in HTML | Retry with different headers; check geo/age restrictions |
| `NResolverError` | Cannot find/compute n-function | Use URL without n; may get throttled |
| `SignatureCipherError` | Cannot decipher signature | Cannot download this format; try another format |
| `StreamResolutionError` | No valid formats in response | Video may be unavailable; check playabilityStatus |
| `DownloadError` | HTTP error during download | Retry with backoff; try alternative URL |

---

## 13. References

- tyrrrz.me/blog/reverse-engineering-youtube (2023) — Detailed analysis of YouTube API
- yt-dlp/yt-dlp extractor/youtube.py — Reference implementation for extraction patterns
- TeamNewPipe/NewPipeExtractor — Java implementation with n-param and cipher extraction
- YouTube.js (LuanRT) — TypeScript reference with full player response types
- CipherDropX (Klypse) — Python regex-only cipher deciphering library
- MetroFuse (956tris) — Kotlin implementation with Q-array obfuscation handling
