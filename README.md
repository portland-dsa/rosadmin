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
uv run rosadmin one-shot test-create-group --no-delete-at-end
```

Just make sure to delete the group manually if you don't want it there.

## Setting up the **Dev** Environment

Now you can run:

```zsh
uv sync --dev
```

To install the dev dependencies. We use `black` for formatting. I use `pylance` for type checking, but any compatible type checker should work. Pylance/Pyright is recommended if you're contributing, however, because we reject PRs with warnings that may be exclusive to it!

The dev dependencies also include `google-api-python-client-stubs` which is an unofficial but maintained library of type stubs for the Google API. If your editor and language server isn't cooperating, a quick `reveal_type` on whatever is failing usually fixes it.

We also use both `pytest` and `behave`. For testing run

```
uv run pytest
uv run behave
```

Add the `--tags live` if you have a key and want to run any tests that hit an actual server somewhere. The pre-push commit hooks run the live tests, you can push with `--no-verify` if you haven't been given them.

If you're a front-end dev developing against the backend:

`zsh|bash|etc`:
```zsh
ROSADMIN_FAKE_LOGIN=1 uv run rosadmin serve
```

`powershell`
```pwsh
$env:ROSADMIN_FAKE_LOGIN="1"; uv run rosadmin serve
```

This enables the fake login entry-point for developing against for test purposes. Note: to test anything significant you'll need a DWD-enabled service account key for some sort of (ideally) isolated staging workspace account hierarchy.

**Second Note** if you're running the SPA from Vite's dev server, you probably want a proxy entry `server.proxy: { "/api": "https:/127.0.0.1:8000" }` so it behaves just like a real ~~boy~~ deployment. Otherwise you'll get weird CORS issues since the requests aren't proxied like they are on the live box.

# Questions You Could Theoretically Ask

1. Why `rosadmin`?

Original name was boring and also limiting (it was Google Workspace Sync). [Botonio Botsci](https://github.com/portland-dsa/botonio-botsci) ended up being the name for our Discord bot by suggestion, named after Antonio Gramsci. We ended up not using another suggestion - Rosa Luxembot, so instead the admin service was named Rosadmin after her! (Then we committed to the bit and the common deploy infrastructure is [Che Deploya](https://github.com/portland-dsa/che-deploya) after Che Guevara).

# Contributing

Make sure all your commits are signed, and don't be an asshole or a bigot in your issues and PRs.

# License

This project is licensed under the AGPL v3.0. Any derivatives are required to use this copyleft license. Please see the [LICENSE](./LICENSE) file for more information.