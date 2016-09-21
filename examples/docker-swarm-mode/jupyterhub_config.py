# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

# Configuration file for JupyterHub
import os
from docker.types import Mount

c = get_config()

# Start hub on all interfaces within the JupyterHub container
# Single-user notebook containers will access hub by service name on
# the Docker network by default
c.JupyterHub.hub_ip = '0.0.0.0'
c.JupyterHub.hub_port = 8080
# Start proxy on all interfaces within container
c.JupyterHub.proxy_api_ip = '0.0.0.0'

# TLS config
c.JupyterHub.port = 443
c.JupyterHub.ssl_key = os.environ['SSL_KEY']
c.JupyterHub.ssl_cert = os.environ['SSL_CERT']

# Authenticate users with GitHub OAuth
c.JupyterHub.authenticator_class = 'oauthenticator.GitHubOAuthenticator'
c.GitHubOAuthenticator.oauth_callback_url = os.environ['OAUTH_CALLBACK_URL']

# Persist hub data on volume mounted inside container
data_dir = os.environ.get('DATA_VOLUME_CONTAINER', '/data')
c.JupyterHub.db_url = os.path.join('sqlite:///', data_dir, 'jupyterhub.sqlite')
c.JupyterHub.cookie_secret_file = os.path.join(data_dir,
    'jupyterhub_cookie_secret')

# Whitlelist users and admins
c.Authenticator.whitelist = whitelist = set()
c.Authenticator.admin_users = admin = set()
c.JupyterHub.admin_access = True
pwd = os.path.dirname(__file__)
with open(os.path.join(pwd, 'userlist')) as f:
    for line in f:
        if not line:
            continue
        parts = line.split()
        name = parts[0]
        whitelist.add(name)
        if len(parts) > 1 and parts[1] == 'admin':
            admin.add(name)

# Use custom spawner that creates a Docker service for each notebook server.
c.JupyterHub.spawner_class = 'dockerspawner.DockerSwarmModeSpawner'
# Set http timeout to spawned notebook server > poll interval
c.DockerSwarmModeSpawner.http_timeout = 60
c.DockerSwarmModeSpawner.poll_interval = 30
# Use this Docker image for single-user notebooks
c.DockerSwarmModeSpawner.notebook_image = os.environ['DOCKER_NOTEBOOK_IMAGE']
# Connect containers to this Docker network
network_name = os.environ['DOCKER_NETWORK_NAME']
# Mount the following to each notebook service
# DockerSwarmModeSpawner will use the service name as the mount source name.
# This will create a Docker volume for each service using the service name,
# and mount it as the notebook directory within the container.
mounts = [
    Mount('/home/jovyan/work', # target
          None, # substitute source later
          type='volume',
          driver_config={'name': 'convoy'})
]
# Custom container spec config for notebook service
c.DockerSwarmModeSpawner.container_spec_kwargs = dict(
    # There are "command" inconsistencies between service and container APIs.
    # For services, command overrides the container entrypoint, so we need
    # "tini --" or kernel subprocesses will die.
    # See https://github.com/docker/docker/issues/24196
    command=["tini", "--", 'start-singleuser.sh'],
    args=['--debug'],
    mounts=mounts
)
# Custom service config
c.DockerSwarmModeSpawner.service_kwargs = dict(
    # Notebooks will join this Docker network
    networks=[{
      "Target": network_name
    }],
    # Publish these ports externally to swarm
    endpoint_config={
      "Ports": [
          # spark.ui.port
          {
              "Protocol": "tcp",
              "TargetPort": 4040
          },
          # spark.driver.port
          {
              "Protocol": "tcp",
              "TargetPort": 8080
          },
      ]
    }
)
