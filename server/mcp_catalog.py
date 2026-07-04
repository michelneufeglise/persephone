"""
Curated catalog of FREE Model Context Protocol servers for the Persephone wizard.
All entries are open-source, self-hostable, and have no paid tier.
"""
from __future__ import annotations

# Each entry: { id, name, description, category, install: { command, args, env_vars: { key: { required, description } } }, requires_setup, docs_url }

MCP_SERVERS: list[dict] = [

    # ── Web & Search ────────────────────────────────────────────────────────────
    {
        "id": "fetch",
        "name": "Fetch",
        "category": "web",
        "description": "Retrieve and convert any URL to markdown for the model to read.",
        "tags": ["web", "html", "markdown", "free"],
        "install": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "category": "web",
        "description": "Web + local search via Brave's independent index. 2k free queries/month with a free API key.",
        "tags": ["search", "web", "weather", "news"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env_vars": {
                "BRAVE_API_KEY": {
                    "required": True,
                    "description": "Free Brave Search API key (get one at api.search.brave.com)",
                },
            },
        },
        "requires_setup": True,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    },
    {
        "id": "duckduckgo-search",
        "name": "DuckDuckGo Search",
        "category": "web",
        "description": "Search the web via DuckDuckGo. No API key required, completely free.",
        "tags": ["search", "web", "no-api-key", "free"],
        "install": {
            "command": "uvx",
            "args": ["duckduckgo-mcp-server"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/nickclyde/duckduckgo-mcp-server",
    },
    {
        "id": "puppeteer",
        "name": "Puppeteer (Browser)",
        "category": "web",
        "description": "Drive a headless Chromium browser — navigate, fill forms, screenshot.",
        "tags": ["browser", "automation", "scraping", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
    },

    # ── Local files & system ───────────────────────────────────────────────────
    {
        "id": "persephone-fs",
        "name": "Persephone Filesystem",
        "category": "files",
        "description": "Read & write files inside the Persephone repo (/Users/michelneufeglise/private/persephone). Pre-scoped for Ornith Coder mode.",
        "tags": ["files", "persephone", "ornith", "coding", "essential", "free"],
        "install": {
            "command": "npx",
            "args": [
                "-y", "@modelcontextprotocol/server-filesystem",
                "/Users/michelneufeglise/private/persephone",
            ],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "category": "files",
        "description": "Read & write files, list directories, search, move, rename. Like Finder for the LLM. Restricted to allowed paths.",
        "tags": ["files", "finder", "local", "essential", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents", "~/Downloads", "~/Desktop"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "id": "git",
        "name": "Git",
        "category": "files",
        "description": "Read git history, diffs, and branches in any local repository.",
        "tags": ["git", "local", "version-control", "free"],
        "install": {
            "command": "uvx",
            "args": ["mcp-server-git"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    },
    {
        "id": "persephone-git",
        "name": "Persephone Git Remote",
        "category": "files",
        "description": "Push, pull, and fetch for the Persephone repo — fills the gap left by mcp-server-git, which has no remote operations. Powers Ornith Coder's commit-and-push step.",
        "tags": ["git", "push", "remote", "persephone", "ornith", "free"],
        "install": {
            "command": "python3",
            "args": ["/Users/michelneufeglise/private/persephone/server/mcp_persephone_git.py"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://modelcontextprotocol.io",
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "category": "data",
        "description": "Query and modify SQLite databases directly. Self-contained, no setup.",
        "tags": ["database", "sql", "local", "free"],
        "install": {
            "command": "uvx",
            "args": ["mcp-server-sqlite", "--db-path", "./persephone-notes.db"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
    },

    # ── Knowledge & memory ─────────────────────────────────────────────────────
    {
        "id": "memory",
        "name": "Memory (Knowledge Graph)",
        "category": "knowledge",
        "description": "Persistent entity-relationship memory for long-term recall across sessions.",
        "tags": ["memory", "kg", "long-term", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "category": "knowledge",
        "description": "Lets the model think through complex problems step-by-step with revisions.",
        "tags": ["reasoning", "thinking", "planning", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    },
    {
        "id": "time",
        "name": "Time & Timezone",
        "category": "knowledge",
        "description": "Get current time, convert between timezones, parse dates.",
        "tags": ["time", "utility", "free"],
        "install": {
            "command": "uvx",
            "args": ["mcp-server-time"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    },

    # ── Developer tools ────────────────────────────────────────────────────────
    {
        "id": "github",
        "name": "GitHub",
        "category": "dev",
        "description": "Read public repos, issues, PRs. Free with a personal access token.",
        "tags": ["github", "code", "repo", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env_vars": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": {
                    "required": True,
                    "description": "Your GitHub personal access token (free; generate at github.com/settings/tokens)",
                },
            },
        },
        "requires_setup": True,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    },
    {
        "id": "gitlab",
        "name": "GitLab",
        "category": "dev",
        "description": "Read GitLab repos, issues, merge requests. Free tier supported.",
        "tags": ["gitlab", "code", "repo", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gitlab"],
            "env_vars": {
                "GITLAB_PERSONAL_ACCESS_TOKEN": {
                    "required": True,
                    "description": "Your GitLab personal access token (free)",
                },
                "GITLAB_API_URL": {
                    "required": False,
                    "description": "Custom GitLab instance URL (default: https://gitlab.com)",
                },
            },
        },
        "requires_setup": True,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab",
    },

    # ── Productivity ───────────────────────────────────────────────────────────
    {
        "id": "everything",
        "name": "Everything (Demo)",
        "category": "knowledge",
        "description": "Reference MCP server with prompts, tools, and resources — great for learning.",
        "tags": ["demo", "reference", "free"],
        "install": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "env_vars": {},
        },
        "requires_setup": False,
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
    },
]


CATEGORIES = [
    {"id": "web",       "name": "Web & Search",      "icon": "globe"      },
    {"id": "files",     "name": "Local Files",       "icon": "folder"     },
    {"id": "data",      "name": "Databases",         "icon": "database"   },
    {"id": "knowledge", "name": "Memory & Thinking", "icon": "brain"      },
    {"id": "dev",       "name": "Developer Tools",   "icon": "code"       },
]


def list_servers() -> list[dict]:
    return MCP_SERVERS
