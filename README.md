# Rosadmin

Synchronizes Google Workspace with membership records. This currently uses a service account with Domain-Wide Delegation enabled. It'll probably do more someday but this is the highest priority by a wide margin.

Tests use a scoped staging credential and run against a mock Solidarity Tech server using fake personas (unfortunately, with real emails because Google Groups) so membership records don't get messed with.

# Setting up Google Workspace & GCP

If you want to set this mess up yourself, here are the instructions so you're not going insane reading the rat's nest of bad Google API docs.

1. Have a Google Workspace for Business account with at least a Starter plan, make sure you have an account with superadmin access.
2. Create a new GCP Project
3. Set up `gcloud` and set your default project to the one you just created. The [docs](https://docs.cloud.google.com/sdk/docs/install-sdk) for this are actually baseline functional so follow those.
4. (OPTIONAL) Turn off all the default services it enables on the project you made for some reason. On Powershell you can do `@(gcloud services list --format="value(config.name)") | %{&gcloud services disable --force $_}`. On a Unix-like you can use `xargs`.
    1. Now you need to go into the Cloud Console and reactivate the [Service Usage API](https://console.cloud.google.com/marketplace/product/google/serviceusage.googleapis.com) if you want to continue using the CLI (sorry), but it's not strictly necessary. The service account won't have access to this endpoint.
    2. You now need to enable the following APIs (either via the CLI with `gcloud services enable` or the web UI). I'll explain each in a below section:
       1. [Admin SDK API](https://console.cloud.google.com/marketplace/product/google/admin.googleapis.com) (`gcloud services enable admin.googleapis.com`)
       2. [Cloud Identity API](https://console.cloud.google.com/marketplace/product/google/cloudidentity.googleapis.com) (`gcloud services enable cloudidentity.googleapis.com`)
       3. [Groups Settings API](https://console.cloud.google.com/marketplace/product/google/groupssettings.googleapis.com) (`gcloud services enable groupssettings.googleapis.com`)
       4. (OPTIONAL) You can enable `logging.googleapis.com` and `drive.googleapis.com` for future work, but neither is necessary in the project's current state.
 5. Go to [APIs & Services/credentials](https://console.cloud.google.com/apis/credentials) and click "Manage Service Accounts", then "+ Create Service Account". Give it whatever name, ID, and description you want. It does **not** need any permissions or principals with access (unless you already have a robust IAM setup in which case you probably don't need this guide).
 6. From the [Service Accounts Panel](https://console.cloud.google.com/iam-admin/serviceaccounts), click on your new service account.
    1. Go to Keys and click Add key -> Create new Key. Pick Json. Save it. Keep it secret, keep it safe. You probably want to use a secrets manager if you have one.
    2. Go back to "details" and hit Expand "Advanced settings" and click the link under "Domain-wide Delegation" (DWD) that says "View Google Workspace Admin Console". Keep this tab open so you can copy the "Client ID" value. The yellow warning is true and unfortunate, we need this.
 7. For ~some reason~ it doesn't take you to the actual page to set up DWD. Go to [Security -> Access and data control -> API controls](https://admin.google.com/u/1/ac/owl?journey=218) and hit "MANAGE DOMAIN WIDE DELEGATION". Now hit "Add new" Enter the Client ID from the GCloud page from the previous step. Add the following scopes (you can just copy+paste the text under 1 below):
    1. `https://www.googleapis.com/auth/admin.directory.group.readonly, https://www.googleapis.com/auth/admin.directory.group.member.readonly, https://www.googleapis.com/auth/admin.directory.group, https://www.googleapis.com/auth/cloud-identity.groups, https://www.googleapis.com/auth/apps.groups.settings`
    2. You may OPTIONALLY add `https://www.googleapis.com/auth/drive.admin` for future purposes, but currently it is not strictly required.
    3. These represent the actual API endpoints we'll be hitting within the APIs we enabled above for our GCP project.

## Explanation of APIs & Services & DWD

For some reason, working with Google Groups within Google Workspace requires the most absurd and labyrinthine network of APIs in the universe.

You might say "why not just use the Cloud Identity API?" WRONG, INCORRECT. The docs make this seems like a correct choice. It is not. Those groups are for *service and impersonator accounts within a GCP project*. If you want actual Google Groups for, say, Google Drive purposes, you instead CLEARLY need the `admin/directory_v1` endpoint you idiot, you fool because ???

"But why is the Cloud Identity API still in there?" We need it for one purpose: you can only create mailing list groups via `admin/directory_v1`. If you want to change it to a `security group`, you need to set the `labels` of the group. The `labels` property can only be modified from the Cloud Identity API.

The Groups Settings API is there because, for some reason, neither of these other APIs can modify the settings and permissions of Google Groups. In order to do things such as make a group invite only, disable posting in it, allowing members to see the list of emails in the group, and other such VERY IMPORTANT THINGS you must use an entirely separate API that exists purely and only for this purpose.

Domain-wide Delegation (DWD) is necessary. Why? Well, OAuth 2.0 expires frequently. For a long-running service, you need to enable DWD on a service account. This has some drawbacks because DWD allows the service account to impersonate the superadmin and operate on their behalf. This isn't super important when only managing groups, but will be integral to managing drives organization-wide later. For *testing* an OAuth 2.0 token linked to a testing user is preferable, and will be added to this project later.

# Setting up the Runtime Environment

Either you're unlucky and are setting this up yourself. In which case, see the above section, or you're in our org and got handed a key file in Vaultwarden. Congrats!

Luckily, setting up the project is pretty easy. Firstly, [install the package manager uv](https://docs.astral.sh/uv/getting-started/installation) via your preferred method.

If you want to test the program, set one of two environment variables:

- `CREDENTIALS_JSON` is the raw `json` string representing the key file for the service account you created (see step 6.1) or were given. **If you're doing this in the terminal remember not to paste the contents**. Do something like `CREDENTIALS_JSON=$(cat keyfile.json)`. This is mostly meant for CI/CD environments and should be in a Secret.
- `CREDENTIALS_FILE` is a path to the key file you downloaded (see step 6.1) or were given.

After you set that environment variable, run:
```zsh
uv run rosadmin one-shot test-group-lifecycle
```

With luck, this will create a group for you, add a member, then grab the group info to print and delete the group. If this crashes after creating the group (say you forgot to enable the Cloud Identity API), it's smart enough to delete it at the start before trying again, so don't worry about doing that manually.

If you don't want to delete the test group at the end (say you want to go into the admin panel and inspect it), you can run

```zsh
uv run rosadmin one-shot test-group-lifecycle --delete-at-end=False
```

Just make sure to delete the group manually if you don't want it there.

## Setting up the **Dev** Environment

Now you can run:

```zsh
uv sync --dev
```

To install the dev dependencies. We use `ruff` for formatting. I use `pylance` for type checking, but any compatible type checker should work. Pylance/Pyright is recommended if you're contributing, however, because we reject PRs with warnings that may be exclusive to it!

The dev dependencies also include `google-api-python-client-stubs` which is an unofficial but maintained library of type stubs for the Google API. If your editor and language server isn't cooperating, a quick `reveal_type` on whatever is failing usually fixes it.

We also use both `pytest` and `behave`. For testing run

```zsh
uv run pytest
uv run behave
```

Add the `--tags live` if you have a key and want to run any tests that hit an actual server somewhere. The pre-push commit hooks run the live tests, you can push with `--no-verify` if you haven't been given them.

Note: to test anything significant you'll need a DWD-enabled service account key for some sort of (ideally) isolated staging workspace account hierarchy.

### Serving

To *serve* `rosadmin` you need two values: an HMAC key, and a Database.

To get an hmac key:
```zsh
openssl rand -hex 32
```

Store this for later.

Then, set up the database:
```zsh
podman compose -f deploy/test-infra/compose.yaml up -d
uv run yoyo apply -b --no-config-file -d postgresql+psycopg://rosadmin_app@127.0.0.1:54432/rosadmin_dev rosadmin/migrations
```

Then, set up a `.env` file as such:

```
ROSADMIN_DB_DSN="host=127.0.0.1 port=54432 dbname=rosadmin_dev user=rosadmin_app"
ROSADMIN_AUDIT_HMAC_KEY=<the key from openssl>
```

Now you can launch `rosadmin`:
```zsh
uv run --env-file .env rosadmin serve --port <port>
```

Note that to do anything useful you should follow the steps in `SSO Flow` below.

### SSO Flow

#### Fake Login

If you're a front-end dev developing against the backend:

`zsh|bash|etc`:
```zsh
ROSADMIN_FAKE_LOGIN=1 uv run --env-file .env rosadmin serve
```

`powershell`
```pwsh
$env:ROSADMIN_FAKE_LOGIN="1"; uv run --env-file .env rosadmin serve
```

**Or** you can also add `ROSADMIN_FAKE_LOGIN=1` to your `.env` file.

This enables the fake login entry-point for developing against for test purposes.

You can then trigger a fake login to test with:
```zsh
curl -i localhost:8000/api/auth/fake-login -c jar.txt -X POST -H 'content-type: application/json' -d '{"persona": "leader"}'
curl -i localhost:8000/api/me -b jar.txt
```

**Second Note** if you're running the SPA from Vite's dev server, you probably want a proxy entry `server.proxy: { "/api": "http://127.0.0.1:8000" }` so it behaves just like a real ~~boy~~ deployment. Otherwise you'll get weird CORS issues since the requests aren't proxied like they are on the live box.

#### Real Login

First of all, follow the instructions in the `README.md` at https://github.com/portland-dsa/botonio-botsci - it is not repeated here because it is *very* involved.

In short, what you need on *this* end:

- The **public key** from `cargo run -p discord-bot --example sso_keygen`. *Make sure this is the pair that matches `BOT_SSO_SIGNING_KEY` from the same run of that command*
- The **bearer token** assigned to `BOT_SSO_CALLER_BEARER`
- The **socket path** assigned to `BOT_SSO_SOCKET_PATH`
- The **redirect URL** assigned to `BOT_SSO_REDIRECT_URI`
- The **guild ID** assigned to `DISCORD_GUILD_ID` (the same guild your test bot is in)
- The values from `.env` in the [Serving](#serving) section above
- The database from [Serving](#serving) above up and migrated **on the same wsl or Linux instance as the bot**

Note that the **redirect URL** is very important, because whatever `port` you put there is the same port you must run `rosadmin` on! So if you set it to `localhost:9999/api/auth/callback` you must run rosadmin with `--port 9999`.

Set up your `.env` file like so:

```
BOTONIO_SSO_PUBLIC_KEY=<twin to BOT_SSO_SIGNING_KEY>
BOTONIO_SSO_BEARER=<same as BOT_SSO_CALLER_BEARER>
BOTONIO_SSO_SOCKET_PATH=<same as BOT_SSO_SOCKET_PATH>
BOTONIO_SSO_GUILD_ID=<same as DISCORD_GUILD_ID>
BOTONIO_SSO_AUD=rosadmin
BOTONIO_SSO_ISS=botonio
BOTONIO_SSO_KID=v1

ROSADMIN_FAKE_LOGIN=1 # Optional
ROSADMIN_DB_DSN="host=127.0.0.1 port=54432 dbname=rosadmin_dev user=rosadmin_app"

ROSADMIN_AUDIT_HMAC_KEY=<from the serving section or `openssl rand -hex 32`>
```

Remember, this all *must* be done *either* in the *same* WSL instance, *or* on the same Linux box(/virtual machine)

Now here's your takeoff checklist:
- [ ] Run the **Bot**
  - [ ] Have your `.env`, Guild, and Discord application set up as per the Botonio Botsci README
  - [ ] Run the database from the `botonio-botsci` repo root with `podman compose -f deploy/test-infra/compose.yaml up -d`
  - [ ] Migrate the database with `cargo sqlx migrate run --source crates/persistence/migrations`
  - [ ] Run the bot with `cargo run --bin botonio-botsci`
  - [ ] Run `/setup` in the Discord test server and set the options as in the Botonio Botsci README
  - [ ] Ctrl+C to stop the bot (do NOT kill the database or you'll have to repeat the previous step)
  - [ ] Run the bot with `cargo run --bin botonio-botsci`
- [ ] Run **rosadmin**
  - [ ] Have your `.env` set up as above
  - [ ] Run the database from the `rosadmin` repo root with `podman compose -f deploy/test-infra/compose.yaml up -d`
  - [ ] Migrate the database with `uv run yoyo apply -b --no-config-file -d postgresql+psycopg://rosadmin_app@127.0.0.1:54432/rosadmin_dev rosadmin/migrations`
  - [ ] Run rosadmin with `uv run --env-file .env rosadmin serve --port <IMPORTANT THE SAME PORT AS BOT_SSO_REDIRECT_URI>`

Now you can communicate via the API endpoints as per the specification, either by hosting the frontend, or with a browser. Note that `curl` isn't very useful because of a lack of ways to grab a Discord OAUTH authorization from the terminal (due to CSRF protection, you can't *start* a session from the terminal, then *finish* in the browser just to reuse the session state in the terminal).

You can verify SSO works by logging in with `localhost:<PORT>/api/auth/begin`, then authorize your Discord account and, with luck, it will redirect you to a blank 404 at `localhost:<PORT>`.

Phew, thanks for enduring this marathon. Luckily other than the bot `/setup` pain you really only need to do most of this once.

**Reminder** if you're running the SPA from Vite's dev server and pointing it at the now running `rosadmin`, whether using SSO or `fake-login`, you probably want a proxy entry `server.proxy: { "/api": "http://127.0.0.1:<PORT>" }` so it behaves just like a real ~~boy~~ deployment. Otherwise you'll get weird CORS issues since the requests aren't proxied like they are on the live box. (Same reason starting in the terminal and then using the browser won't work).

# Questions You Could Theoretically Ask

1. Why `rosadmin`?

Original name was boring and also limiting (it was Google Workspace Sync). [Botonio Botsci](https://github.com/portland-dsa/botonio-botsci) ended up being the name for our Discord bot by suggestion, named after Antonio Gramsci. We ended up not using another suggestion - Rosa Luxembot, so instead the admin service was named Rosadmin after her! (Then we committed to the bit and the common deploy infrastructure is [Che Deploya](https://github.com/portland-dsa/che-deploya) after Che Guevara).

# Contributing

Make sure all your commits are signed, and don't be an asshole or a bigot in your issues and PRs.

# License

This project is licensed under the AGPL v3.0. Any derivatives are required to use this copyleft license. Please see the [LICENSE](./LICENSE) file for more information.