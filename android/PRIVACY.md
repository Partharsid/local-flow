# LocalFlow for Android: privacy

**By default nothing leaves your phone.** Speech recognition and text cleanup run on the device. The only network use is
downloading the models once, and checking for them again if you remove one.

## Optional: "Help improve LocalFlow"

Off by default, and only available in builds that were given a server address. If you turn it on and accept the
consent text:

- LocalFlow saves each dictation's **recording** and uploads it, with the **text the speech model heard**, the **text it
  typed**, the language mode, model names, app version, device tier and device name, to the LocalFlow server. Uploads wait for Wi-Fi.
- We use these to train better speech models, especially for Hinglish.
- **Never collected:** your name, phone number, e-mail, contacts, location, anything from other apps, or audio from
  password fields. The server keeps no IP addresses. You are identified only by a random id.
- A recording contains your voice, which is personal data. Keep that in mind before you turn it on, and before you dictate
  things you would not want a person at the project to hear if they review samples.
- **Turn it off** any time in the app. **Delete my data** removes everything you shared from the server at once and clears
  what is waiting on the phone.

The server and its limits are in `server/`.
