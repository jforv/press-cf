# Production

Since production is running a custom setup with multiple apps and a fork of
Frappe, we have to be very careful while updating the code. Always make sure to
test your changes on staging.frappe.cloud (You'll find it on Frappe Cloud!)
before deploying to production.

> **NOTE**: Never do `bench update` on production. Always manually pull each app's repo and run necessary commands.

## Updating Press on Production

```sh
cd ~/frappe-bench/apps/press
```

```sh
git pull origin master
```

If migrate is required

```sh
bench migrate
```

If web workers need to be restarted, **DON'T** run `bench restart --web`. Do this instead:

```sh
sudo supervisorctl signal SIGHUP frappe-bench-web:
```

This will [tell Gunicorn](https://docs.gunicorn.org/en/stable/signals.html) to reload the application without restarting the whole web worker process, which is much faster and less disruptive.

If background workers need to be restarted, **DON'T** run `bench restart`. (Since this will also restart web workers, which we don't want.) Do this instead:

```sh
sudo supervisorctl restart frappe-bench-workers:
```

Usually this is what you need. Since we have some frequent time consuming jobs in a separate worker group, we often want to avoid restarting that group. If, your code change affects that group, you can also do:

```sh
sudo supervisorctl restart frappe-bench-chill-workers:
```

You can see what all worker groups you have by running:

```sh
sudo supervisorctl status all
```

## Frappe Fork

Production is currently running a fork of Frappe due to various reasons. This is not ideal, but it is what it is.

The fork is available [here](https://github.com/balamurali27/frappe/tree/fc-prod)

### Updating Frappe on Production

Firsly, you need to update the fork by creating a pull request from the main [Frappe](https://github.com/frappe/frappe) repo to the fork. Once the PR is merged, you can pull the changes on production.

```sh
cd ~/frappe-bench/apps/frappe
```

```sh
git pull origin fc-prod
```

Now, you can run the same commands as mentioned in the previous section to migrate and restart workers if needed. Usually, you need to restart all workers since Frappe is the core framework and any change in it can affect all apps.

## Updating supervisor conf

We also use a custom supervisor conf for production, which is available [here](../supervisor.conf). You can make changes to this file and push. Then:

```sh
cd ~/frappe-bench/config
```

Make a copy of the existing conf file on production:

```sh
sudo cp supervisor.conf supervisor.conf.bak
```

Then pull the changes:

```sh
cd ~/frappe-bench/apps/press
```

```sh
git pull origin master
```

```
cd ~/frappe-bench/config
```

Copy the new conf file to the config directory:

```sh
sudo cp ~/frappe-bench/apps/press/supervisor.conf .
```

Then ask supervisor to reread the conf files and update the running processes:

```sh
sudo supervisorctl reread
```

```sh
sudo supervisorctl update
```

> **NOTE**: This will take some time

---

## Single Droplet Setup (DigitalOcean + Cloudflare)

This section covers deploying Press on a **single unified DigitalOcean Droplet**
where the app server, MariaDB, Redis, and Nginx all run on the same machine
(`is_unified_server = True`). Horizontal scale-out adds app-only Droplets
on demand via the existing `AutoScaleRecord` machinery.

### Recommended Droplet Specs

| Use case | vCPU | RAM | Disk |
|---|---|---|---|
| Small (\<50 managed sites) | 4 | 16 GB | 100 GB SSD |
| Medium (50–200 sites) | 8 | 32 GB | 200 GB SSD |
| Large (200+ sites / scale-out) | 16 | 64 GB | 400 GB SSD |

Create the Droplet with **Ubuntu 22.04**, enable **private networking** (VPC),
and attach a **Block Volume** (≥100 GB) if you intend to scale out later.

### Phase 1 — Install Press

```sh
# 1. Install bench
pip install frappe-bench

# 2. Initialise bench (Frappe v15)
bench init frappe-bench --frappe-branch version-15
cd frappe-bench

# 3. Install Press app
bench get-app press https://github.com/frappe/press
bench --site <your-site> install-app press

# 4. Copy deployment config
cp apps/press/deployment/common_site_config.json sites/common_site_config.json
cp apps/press/deployment/supervisor.conf config/supervisor.conf

# 5. Apply supervisor config
sudo supervisorctl reread && sudo supervisorctl update
```

### Phase 2 — Configure Press Settings

In the Press admin UI (`/app/press-settings`), set:

| Field | Value |
|---|---|
| `dns_provider` | `Cloudflare` |
| `cloudflare_api_token` | Your Cloudflare API token (Zone:Edit) |
| `cloudflare_zone_id` | Your Cloudflare Zone ID |
| `docker_registry_url` | `registry.digitalocean.com` |

> **Cloudflare DNS note**: Set all Press-managed CNAMEs to **DNS-only (grey cloud)**
> in Cloudflare. Press requires direct IP access and does not work behind Cloudflare
> proxy/orange-cloud mode.

### Phase 3 — Enable Single-Server Auto-Scaling

Run the one-time setup script:

```sh
bench --site <your-site> execute press.deployment.setup_single_server.execute
```

This enables RAM-aware worker allocation (`new_worker_allocation`) on all servers
and opts all active benches into dynamic scaling (`auto_scale_workers`).

To also create Prometheus CPU alert rules (requires a secondary server configured):

```sh
bench --site <your-site> execute press.deployment.setup_single_server.execute \
  --kwargs '{"setup_prometheus": true, "scale_up_cpu_threshold": 80, "scale_down_cpu_threshold": 30}'
```

### Phase 4 — Shared Volume for Scale-Out (Optional)

Required only if you want Press to spin up additional app-only Droplets automatically.

```sh
# On the primary Droplet — attach a DO Block Volume first, then:
ansible-playbook -i "<primary_private_ip>," \
  apps/press/press/playbooks/nfs_do_volume.yml \
  -e "do_volume_device=/dev/disk/by-id/scsi-<volume-id>" \
  -e "nfs_export_network=<your-vpc-cidr>"
```

In Press Settings, set `shared_directory` to `/home/frappe/benches`.

Then configure a secondary Server record in Press (app-only Droplet) and call
`Server.add_automated_scaling_triggers()` via the Press dashboard.

### Verification

```sh
# Check worker counts updated dynamically
bench --site <your-site> execute press.press.doctype.server.server.scale_workers \
  --kwargs '{"now": true}'

# Confirm Cloudflare DNS sync is working
# Create a test site → verify CNAME appears in Cloudflare dashboard

# Check supervisor processes
sudo supervisorctl status all

# Monitor auto-scale queue
bench --site <your-site> execute frappe.utils.background_jobs.get_jobs \
  --kwargs '{"site": "<your-site>", "queue": "auto-scale"}'
```
