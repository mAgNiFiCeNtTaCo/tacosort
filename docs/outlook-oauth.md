# Outlook.com / Hotmail: signing in with Microsoft (OAuth2)

Microsoft no longer lets mail programs log in to Outlook.com, Hotmail, Live or MSN addresses with a
password, not even an app password. Programs must use Microsoft's own sign-in ("OAuth2"): you approve
TacoSort once in your browser, and Microsoft gives it a key that only reads and organises mail.

To use that sign-in, a program needs a **client ID**: the ID of an "app registration" at Microsoft.
Open-source programs like TacoSort can't safely ship one for everybody, so you create your own, once.
It is free, needs no credit card, and takes about 10 minutes.

## 1. Create the app registration

1. Go to [entra.microsoft.com](https://entra.microsoft.com) and sign in with your Outlook.com /
   Hotmail account. (The older address [portal.azure.com](https://portal.azure.com) > *App
   registrations* works too.)
2. Open **Applications** > **App registrations** > **New registration**.
3. Fill in:
   - **Name:** `TacoSort` (anything you like).
   - **Supported account types:** *Personal Microsoft accounts only*. If you also want work or school
     accounts, pick *Accounts in any organizational directory and personal Microsoft accounts*.
   - **Redirect URI:** leave empty.
4. Click **Register**.
5. In the new app, open **Authentication**. Under *Advanced settings*, set **Allow public client
   flows** to **Yes**, and click **Save**. (This is what allows the "type a code" sign-in.)
6. Optional but tidy: **API permissions** > *Add a permission* > *APIs my organization uses* (or
   *Microsoft Graph*) > find **Office 365 Exchange Online** > *Delegated* > **IMAP.AccessAsUser.All**.
   For personal accounts this permission is requested at sign-in anyway, so you can skip this step.
7. On the app's **Overview** page, copy the **Application (client) ID**. It looks like
   `00000000-1111-2222-3333-444444444444`.

A client ID is not a password: it only says which app is asking. Don't create a *client secret*;
TacoSort doesn't need one.

## 2. Sign in from TacoSort

Run `tacosort setup` (or `tacosort accounts add`) and type your Outlook.com address. TacoSort asks
for the client ID; paste it. Then it shows something like:

```
To sign in, open https://microsoft.com/devicelogin and enter the code ABCD-EFGH
```

Open that page (on any device), type the code, sign in, and approve the request. TacoSort waits,
then tests the connection. You only do this once: TacoSort keeps a *refresh token* in your computer's
keychain (never in the settings file) and renews access by itself.

## When it stops working

- **You changed your Microsoft password**, or removed TacoSort's access at
  [account.live.com/consent/Manage](https://account.live.com/consent/Manage): run
  `tacosort accounts remove ID` then `tacosort accounts add` to sign in again.
- **"AADSTS7000218 ... client_assertion or client_secret"**: *Allow public client flows* is still
  off (step 1.5).
- **"AADSTS50020" / "account does not exist in tenant"**: the registration's *Supported account types*
  doesn't include personal accounts (step 1.3). Change it under **Authentication**, or create a new
  registration.
- **Work or school account refused**: your organisation may block apps it hasn't approved; ask your
  administrator to allow the app or IMAP access.

## In the settings file

```yaml
  - id: hotmail
    email: me@hotmail.example.com
    provider: outlook
    auth: oauth2-microsoft
    oauth_client_id: 00000000-1111-2222-3333-444444444444
```
