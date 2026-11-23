# Silo Server snap

Snap packaging for [Silo Server](https://github.com/Silo-Server/silo-server), a
self-hosted media server with a web interface and Jellyfin-compatible APIs.

The snap includes PostgreSQL 18 with pgvector, Jellyfin FFmpeg 8, libvips and
Node.js 24. It runs with strict confinement on core24 and currently supports amd64.

## Install from the Snap Store

Install the development build from the edge channel:

```sh
sudo snap install silo-server --edge
sudo snap connect silo-server:removable-media
```

Open [http://localhost:8090](http://localhost:8090) and create your account.
PostgreSQL starts automatically; no separate database installation is needed.

Edge builds track upstream `main` and may contain breaking changes. Back up your
data before updating.

## Configuration

The web interface listens on port 8090. The compatibility listeners default to
ports 8096 (Jellyfin) and 13378 (Audiobookshelf). To change the ports:

```sh
sudo snap set silo-server port=8090 jellyfin-port=8096 audiobookshelf-port=13378
```

Check service status and logs with:

```sh
sudo snap services silo-server
sudo snap logs silo-server -n=100
```

## PostgreSQL

The default `database.mode=bundled` starts a dedicated cluster. It listens only
on a private Unix socket, uses peer authentication, and does not take host port
5432. Both services drop privileges to snapd's `_daemon_` account. As in
[Silo's omnibus image](https://github.com/Silo-Server/silo-server/blob/main/omnibus/rootfs/etc/s6-overlay/scripts/init-postgres),
the `silo` database role owns the dedicated cluster and can run migrations and
create extensions. The server starts after PostgreSQL is accepting connections.

To use an existing PostgreSQL server:

```sh
sudo snap set silo-server database.mode=external \
  database.url='postgresql://silo:URL_ENCODED_PASSWORD@db.example:5432/silo?sslmode=require'
```

Prepare the `silo` database and its owner first, install pgvector on that server,
and permit the required extension creation/migrations. PostgreSQL 18 matches
the bundled server and backup client. For verified TLS, use `sslmode=verify-full`
and an `sslrootcert` file readable inside the snap, for example under
`/var/snap/silo-server/common/silo/`. Avoid recording real passwords in shell
history. A successful configuration change restarts the services; external mode
leaves the bundled PostgreSQL stopped.

Switch back with:

```sh
sudo snap set silo-server database.mode=bundled
```

Switching selects a database; it does **not** copy or migrate data between them.
The previous bundled cluster is retained. A newly selected empty database runs
onboarding again. When connecting an existing Silo installation, restore its
original encryption key before starting against that database.

Redis and Meilisearch are not bundled. The integrated server can run without
Redis, but features such as Watch Party need it. An external Redis is optional:

```sh
sudo snap set silo-server redis.url='redis://127.0.0.1:6379'
```

## Media, state and backups

Put media under `/mnt`, `/media` or `/run/media` and connect `removable-media`.
The `_daemon_` account also needs filesystem read/traverse access (world-readable
media or an appropriate ACL). Select those paths in Silo's library setup.
Paths such as `/srv/media` and arbitrary home directories are outside this snap's
interfaces; bind-mount them under `/mnt` if necessary. Graphics device access is
provided through the `opengl` interface.

Persistent files live in `/var/snap/silo-server/common/`:

| Path | Contents |
| --- | --- |
| `postgresql/data/` | Bundled PostgreSQL cluster |
| `silo/` | Artwork, plugins, caches, downloads and transcoding files |
| `secret-key` | Root-readable encryption master key, generated once at install |

The snap maps `/var/lib/silo` to its persistent `silo/` directory for upstream
defaults. `SNAP_COMMON` survives refreshes and reverts. Reverting the snap does
**not** roll back database migrations. PostgreSQL's major version is fixed at 18;
the launcher refuses incompatible clusters instead of replacing them.

Make a logical backup (works with either database mode):

```sh
sudo snap run silo-server.pg-dump --format=custom > silo.dump
```

Back up `secret-key` separately and securely: the database dump cannot recover
encrypted credentials without it. Back up persistent Silo files too. Stop the
snap before taking a filesystem copy of the PostgreSQL cluster; use `pg_dump`
for a live database.

## Build from source

Install [Snapcraft](https://snapcraft.io/snapcraft) and configure
[LXD](https://documentation.ubuntu.com/lxd/latest/getting-started-cli/) before
building:

```sh
git clone https://github.com/popey/silo-server-snap.git
cd silo-server-snap
snapcraft --use-lxd
```

The recipe fetches upstream `main`, builds Silo and its embedded web interface,
and records the source commit in the snap version and
`usr/share/doc/silo-server/upstream-revision`. It uses the official PGDG and
Jellyfin apt repositories for runtime dependencies.

Install the resulting local snap using its filename:

```sh
sudo snap install ./silo-server_<version>_amd64.snap --dangerous
sudo snap connect silo-server:removable-media
```

Replace `<version>` with the version printed by Snapcraft. Snapcraft caches
sources between builds; to fetch a newer upstream commit, clean Silo's part:

```sh
snapcraft clean silo --use-lxd
snapcraft --use-lxd
```

## Development checks

With ShellCheck and Python 3 installed, run:

```sh
shellcheck -x snap/hooks/* snap/local/bin/* snap/local/lib/common.sh
python3 -m unittest discover -s tests -v
```

The tests exercise installation hooks, configuration, database selection and
launcher behaviour with simulated snapctl, privilege dropping and database
executables. Test an installed snap separately for confinement and playback.
