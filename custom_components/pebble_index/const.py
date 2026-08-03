"""Constants for the pebble_index integration."""

DOMAIN = "pebble_index"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_ENCRYPTION_KEY = "encryption_key"

# Public Firebase project identifiers of the Pebble mobile app / index.repebble.com.
# The API key is not a secret: it identifies the project and is shipped in every
# web bundle. Access is granted purely by the user's own refresh token.
FIREBASE_API_KEY = "AIzaSyBZ9Cdvwwv9At2lPmc8TxyyEqSXGXejGvc"
FIREBASE_PROJECT_ID = "coreapp-ce061"

INDEX_WEB_URL = "https://index.repebble.com"

TOKEN_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"
FIRESTORE_URL = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
    "/databases/(default)/documents"
)

UPDATE_INTERVAL_SEC = 30
REQUEST_TIMEOUT_SEC = 15
