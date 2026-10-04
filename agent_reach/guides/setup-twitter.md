# Twitter Advanced Features Setup Guide (twitter-cli)

Basic Twitter reading is free through Jina Reader and needs no setup.

Advanced features need twitter-cli (@public-clis/twitter-cli):

- Search tweets (`twitter search`)
- Read full tweets and conversation threads (`twitter tweet`, `twitter thread`)
- User timelines (`twitter timeline`)
- Long-form article reading (`twitter article`)

twitter-cli is a free open-source tool (installed with pipx), but it needs your Twitter account cookies.

## Quick setup

1. Check whether twitter-cli is installed:

```bash
which twitter && echo "installed" || echo "not installed"
```

2. Install twitter-cli:

```bash
pipx install twitter-cli
```

3. Confirm the command is installed (no authenticated request is made at this point):

```bash
twitter --help
```

## Getting cookies (Cookie-Editor method, recommended)

1. Install the [Cookie-Editor](https://cookie-editor.com/) browser extension
2. Log in to x.com
3. Click the Cookie-Editor icon → Export → Header String
4. Run the configure command:

```bash
agent-reach configure twitter-cookies
```

This extracts `auth_token` and `ct0` and saves them securely to
`~/.agent-reach/config.yaml`, so `agent-reach doctor` can check that the explicit credentials are complete.
`doctor` does not run `twitter status`, does not verify live that the account works, and does not modify the current shell.

By default it only writes `~/.agent-reach/config.yaml`. Only when the user explicitly agrees to copy the credentials and you explicitly add
`--sync-legacy-twitter` will it also write:

- `~/.config/xfetch/session.json`
- `~/.config/bird/credentials.env`

```bash
agent-reach configure twitter-cookies --sync-legacy-twitter
```

`agent-reach uninstall` only reminds you about these legacy copies and does not delete them automatically. To clean them up,
get the user's confirmation first, then delete the two files above by hand.

`twitter` is a separate upstream command and does not read Agent Reach's config file. When running
`twitter status/search/read/...` directly, you must explicitly set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0`
in the current shell or subprocess environment, as described in the next section. Do not rely on it reading browser cookies automatically.

## Setting cookies manually

If you already know your `auth_token` and `ct0`:

1. Install twitter-cli (if not installed): `pipx install twitter-cli`

2. Set the environment variables:

```bash
export TWITTER_AUTH_TOKEN="your_auth_token"
export TWITTER_CT0="your_ct0"
```

3. Test:

```bash
twitter search "test" -n 1
```

## Proxy configuration

> twitter-cli supports proxies through environment variables:

```bash
export HTTP_PROXY="http://user:pass@host:port"
export HTTPS_PROXY="http://user:pass@host:port"
twitter search "test" -n 1
```

You can also use a global proxy tool:

```bash
proxychains twitter search "test" -n 1
```

## Fallback: bird CLI

If you have already installed the [bird CLI](https://www.npmjs.com/package/@steipete/bird) (`npm install -g @steipete/bird`), it works too. Agent Reach automatically detects and uses an installed bird. The two have similar features; twitter-cli is the currently recommended option.
