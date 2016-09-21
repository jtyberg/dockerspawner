
Deploy JupyterHub on a cluster of Docker Engines running in swarm mode.

## Setup Docker Swarm

These instructions assume that you have already provisioned a Docker Swarm.

List swarm nodes from a swarm manager node.

```
(swarm-manager1)$ docker node ls
ID                           HOSTNAME        STATUS  AVAILABILITY  MANAGER STATUS
0h476mpgzmqipijwnvocttyqe    swarm-worker1   Ready   Active        
19pmjnjzpab29ju6q63ujbqdi *  swarm-manager1  Ready   Active        Leader
9nv8ew0tp10atfo00gxp675pk    swarm-worker2   Ready   Active        
```

## Create an overlay network

Create an [overlay network](https://docs.docker.com/engine/userguide/networking/get-started-overlay/) that JupyterHub containers will use to communicate across swarm nodes.  

```
(swarm-manager1)$ docker network create --driver overlay jupyter
```

## Setup GitHub Authentication

This deployment uses GitHub OAuth to authenticate JupyterHub users.
It requires that you create a [GitHub application](https://github.com/settings/applications/new).
You will need to specify an OAuth callback URL in the following form:

```
https://<myhost.mydomain>/hub/oauth_callback
```

You must pass the Github OAuth secrets to JupyterHub at runtime.
You can do this by setting the `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`,
and `OAUTH_CALLBACK_URL` environment variables when you run the JupyterHub container.

```
(swarm-manager1)$ export GITHUB_CLIENT_ID=<github_client_id>
(swarm-manager1)$ export GITHUB_CLIENT_SECRET=<github_client_secret>
(swarm-manager1)$ export OAUTH_CALLBACK_URL=https://<myhost.mydomain>/hub/oauth_callback
```

## Prepare TLS Certificate and Key Files

Run JupyterHub securely.  Get a TLS certificate chain from [Let's Encrypt](https://letsencrypt.org).  

The following command will pull and run the `letsencrypt` Docker container to generate a certificate chain on the host.  In this deployment, we run the command on the swarm manager node.  Later, we'll constrain JupyterHub to run on the swarm manager node and mount the `/etc/letsencrypt` directory in the JupyterHub container.

Let's Encrypt requires access to port 80, so make sure nothing else is running on that port.  

The command uses the `--staging` flag to obtain a self-signed certificate from the Let's Encrypt staging servers.  Once you've tested it out, remove this flag to get the real thing.

```
(swarm-manager1)$ docker run --rm -it \
    -p 80:80 \
    -v /etc/letsencrypt:/etc/letsencrypt \
    quay.io/letsencrypt/letsencrypt:latest \
    certonly \
    --non-interactive \
    --keep-until-expiring \
    --standalone \
    --standalone-supported-challenges http-01 \
    --agree-tos \
    --force-renewal \
    --domain my.domain \
    --email jtyberg@us.ibm.com \
    --staging
```

## Create a JupyterHub Data Volume

Create a Docker volume to persist JupyterHub users, cookies, etc. across JupyterHub restarts.   This volume will reside on the swarm manager node.  

```
(swarm-manager1)$ docker volume create --name jupyterhub-data
```

## Prepare the Jupyter Notebook Image

You can configure JupyterHub to spawn Notebook servers from any Docker image, as
long as the image has an `ENTRYPOINT` and/or `CMD` that starts a single-user
instance of Jupyter Notebook server that is compatible with JupyterHub.  

To specify the Notebook image to spawn for users, set the value of the  
`DOCKER_NOTEBOOK_IMAGE` environment variable to the desired container image.

Whether you build a custom Notebook image or pull an image from a public or
private Docker registry, the image must reside on each Swarm node.  

If the Notebook image does not exist on a node, Docker will attempt to pull the
image the first time Swarm tries to create a Notebook container on that node.
In such cases, JupyterHub may timeout if the image being pulled is large, so it
is better to pull the image to the nodes before running JupyterHub.  

```
(swarm-manager1)$ docker pull jupyter/pyspark-notebook:1d374670daaa
(swarm-worker1)$ docker pull jupyter/pyspark-notebook:1d374670daaa
(swarm-worker2)$ docker pull jupyter/pyspark-notebook:1d374670daaa
```

## Build JupyterHub

Build JupyterHub Docker image.  These instructions use `docker-machine` and `docker-compose` to build the image on the swarm manager from a local workstation.

1. Create a `userlist` file with a list of authorized users.  At a minimum, this file should contain a single admin user.  The username should be a GitHub username.  For example:

   ```
   echo jtyberg admin >> userlist
   ```

   The admin user will have the ability to add more users in the JupyterHub admin console.

1. Use [docker-compose](https://docs.docker.com/compose/reference/) to build the
   JupyterHub Docker image on the Swarm manager node.

    ```
    eval "$(docker-machine env swarm-manager1)"

    docker-compose build
    ```

## Run JupyterHub

Run the JupyterHub service on the swarm.

```
(swarm-manager1)$ docker service create \
  --name jupyterhub \
  --publish 443:443 \
  --network jupyter \
  --constraint "node.role == manager" \
  --env SSL_KEY="/etc/letsencrypt/live/my.domain/privkey.pem" \
  --env SSL_CERT="/etc/letsencrypt/live/my.domain/cert.pem" \
  --env DOCKER_NETWORK_NAME=jupyter \
  --env DOCKER_NOTEBOOK_IMAGE=jupyter/pyspark-notebook:1d374670daaa \
  --env GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID \
  --env GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET \
  --env OAUTH_CALLBACK_URL=$OAUTH_CALLBACK_URL \
  --mount type=bind,source=/etc/letsencrypt/,target=/etc/letsencrypt \
  --mount type=volume,source=jupyterhub-data,target=/data,volume-driver=local \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  jupyterhub \
  jupyterhub -f /srv/jupyterhub/jupyterhub_config.py --debug
```
