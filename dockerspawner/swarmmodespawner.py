# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import os
import json
import docker
from docker.errors import APIError
from docker.types import (
    ContainerSpec, TaskTemplate
)
from traitlets import (
    Dict,
    Unicode,
    Bool
)
from tornado import gen
from concurrent.futures import ThreadPoolExecutor
from jupyterhub.spawner import Spawner

class DockerSwarmModeSpawner(Spawner):
    """Spawns single-user notebook servers as Docker services on a cluster of
    Docker engines running in swarm-mode.
    """

    _executor = None
    @property
    def executor(self):
        """single global executor"""
        cls = self.__class__
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(1)
        return cls._executor

    _client = None
    @property
    def client(self):
        """single global client instance"""
        cls = self.__class__
        if cls._client is None:
            if self.tls:
                tls_config = True
            elif self.tls_verify or self.tls_ca or self.tls_client:
                tls_config = docker.tls.TLSConfig(
                    client_cert=self.tls_client,
                    ca_cert=self.tls_ca,
                    verify=self.tls_verify,
                    assert_hostname=False)
            else:
                tls_config = None

            docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')
            client = docker.Client(base_url=docker_host, tls=tls_config, version='auto')
            cls._client = client
        return cls._client

    def docker(self, method, *args, **kwargs):
        """Call a docker method in a background thread
        returns a Future
        """
        m = getattr(self.client, method)
        return self.executor.submit(m, *args, **kwargs)

    tls = Bool(False, config=True, help="If True, connect to docker with --tls")
    tls_verify = Bool(False, config=True, help="If True, connect to docker with --tlsverify")
    tls_ca = Unicode("", config=True, help="Path to CA certificate for docker TLS")
    tls_cert = Unicode("", config=True, help="Path to client certificate for docker TLS")
    tls_key = Unicode("", config=True, help="Path to client key for docker TLS")

    @property
    def tls_client(self):
        """A tuple consisting of the TLS client certificate and key if they
        have been provided, otherwise None.
        """
        if self.tls_cert and self.tls_key:
            return (self.tls_cert, self.tls_key)
        return None

    service_id = Unicode()
    service_prefix = 'notebook'
    @property
    def service_name(self):
        return "{}-{}".format(self.service_prefix, self.user.name)

    hub_ip_connect = Unicode(
        "jupyterhub",
        config=True,
        help="""The spawner will configure the notebook containers to use this
        IP to connect to the hub api."""
    )
    notebook_image = Unicode("jupyter/scipy-notebook:2d878db5cbff", config=True)
    container_spec_kwargs = Dict(config=True, help="Container spec args to pass to service create")
    task_config_kwargs = Dict(config=True, help="Task config args to pass to service create")
    service_kwargs = Dict(config=True, help="Service config args to pass to service create")

    @gen.coroutine
    def get_service(self):
        self.log.debug("Getting service '%s'", self.service_name)
        service = None
        try:
            services = yield self.docker('services', filters={'name': self.service_name})
        except docker.errors.NotFound:
            # service does not exist
            self.log.info("Service '%s' is gone", self.service_name)
            service = None
            self.service_id = ''
        except APIError:
            raise
        else:
            if len(services) > 0:
                service = services[0]
                self.service_id = service['ID']
        raise gen.Return(service)

    @gen.coroutine
    def get_service_status(self):
        self.log.debug("Getting tasks for service '%s'", self.service_name)
        try:
            tasks = yield self.docker('tasks', filters={'name': self.service_name})
        except APIError as e:
            self.log.debug("Error retrieving tasks for service '%s': %s", self.service_name, e)
            tasks = []

        if not tasks:
            return None
        elif len(tasks) > 0:
            task = tasks[0] # there should only be one replica
            status = task['Status']
        self.log.debug("service task status '%s'", json.dumps(status, indent=2))
        return status

    @gen.coroutine
    def start(self, name=None, image=None, extra_container_spec_kwargs=None,
        extra_task_config_kwargs=None, extra_service_kwargs=None):
        """Start the single-user notebook as a service"""

        # Individual notebooks are deployed as services, which must have
        # globally unique names within a swarm.
        # Assumes JupyterHub and notebooks are running on same Docker network.
        self.user.server.ip = self.service_name
        self.user.server.port = 8888

        service = yield self.get_service()
        if service is None:
            # Create notebook service
            image = image or self.notebook_image

            # At a minimum, we must pass the JupyterHub environment variables
            # to the notebook container.
            env = ['{}={}'.format(k,v) for k,v in self.get_env().items()]
            _container_spec_kwargs = dict(env=env)
            _container_spec_kwargs.update(self.container_spec_kwargs)
            if extra_container_spec_kwargs:
                _container_spec_kwargs.update(extra_container_spec_kwargs)

            # Set service name as the mount source name.
            mounts = _container_spec_kwargs.get('mounts', None)
            for mount in mounts:
                mount['Source'] = self.service_name
            _container_spec_kwargs['mounts'] = mounts

            container_spec = ContainerSpec(image, **_container_spec_kwargs)

            # TaskTemplate
            _task_config_kwargs = dict()
            _task_config_kwargs.update(self.task_config_kwargs)
            if extra_task_config_kwargs:
                _task_config_kwargs.update(extra_task_config_kwargs)
            task_config = TaskTemplate(container_spec, **_task_config_kwargs)

            _service_kwargs = dict(name=self.service_name)
            _service_kwargs.update(self.service_kwargs)
            if extra_service_kwargs:
                _service_kwargs.update(extra_service_kwargs)

            self.log.debug(
                "Creating service {} with task config {} and kwargs {}".format(
                self.service_name, json.dumps(task_config, indent=2),
                json.dumps(_service_kwargs, indent=2))
            )

            service = yield self.docker('create_service', task_config,
                **_service_kwargs)
            self.service_id = service['ID']
            self.log.info(
                "Created service '{}' (ID: {}) from image {}".format(
                    self.service_name, self.service_id, image))
        else:
            self.log.info(
                "Found existing service '{}' (ID: {})".format(
                    self.service_name, self.service_id))

        # TODO: wait for start?

    def get_env(self):
        env = super(DockerSwarmModeSpawner, self).get_env()

        proto, path = self.hub.api_url.split('://', 1)
        ip, rest = path.split(':', 1)
        hub_api_url = '{proto}://{ip}:{rest}'.format(
            proto = proto,
            ip = self.hub_ip_connect,
            rest = rest
        )

        env.update(dict(
            JPY_USER=self.user.name,
            JPY_COOKIE_NAME=self.user.server.cookie_name,
            JPY_BASE_URL=self.user.server.base_url,
            JPY_HUB_PREFIX=self.hub.server.base_url,
            JPY_HUB_API_URL=hub_api_url
        ))

        return env

    @gen.coroutine
    def stop(self, now=False):
        """Stop the single-user notebook service"""
        # Should we scale tasks to 0 rather than remove it completely?
        self.log.info(
            "Removing service %s (ID: %s)",
            self.service_name, self.service_id)
        try:
            yield self.docker('remove_service', self.service_name)
        except docker.errors.NotFound:
            pass
        self.clear_state()

    @gen.coroutine
    def poll(self):
        """Check if the single-user notebook service is running.

        returns
            None if service is running, or an exit status (0 if unknown)
            if it is not.
        """
        self.log.debug("Polling service %s", self.service_name)
        service_status = yield self.get_service_status()

        if service_status is None:
            status = ""
        else:
            state = service_status['State']
            if state.lower() == 'running':
                status = None
            else:
                status = state
        self.log.debug("Status for service %s: %s", self.service_name, status)
        return status

    def load_state(self, state):
        super(DockerSwarmModeSpawner, self).load_state(state)
        self.service_id = state.get('service_id', None)

    def get_state(self):
        state = super(DockerSwarmModeSpawner, self).get_state()
        if self.service_id:
            state['service_id'] = self.service_id
        return state
