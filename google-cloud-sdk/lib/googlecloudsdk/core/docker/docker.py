# Copyright 2014 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utility library for configuring access to the Google Container Registry.

Sets docker up to authenticate with the Google Container Registry using the
active gcloud credential.
"""

import base64
import json
import os
import subprocess
import sys

from distutils import version as distutils_version
from googlecloudsdk.core import exceptions
from googlecloudsdk.core import log
from googlecloudsdk.core.credentials import store
from googlecloudsdk.core.docker import client_lib
from googlecloudsdk.core.docker import constants
from googlecloudsdk.core.util import files


_USERNAME = 'oauth2accesstoken'
_EMAIL = 'not@val.id'
_CREDENTIAL_STORE_KEY = 'credsStore'
_EMAIL_FLAG_DEPRECATED_VERSION = distutils_version.LooseVersion('1.11.0')


class UnsupportedRegistryError(client_lib.DockerError):
  """Indicates an attempt to use an unsupported registry."""

  def __init__(self, image_url):
    self.image_url = image_url

  def __str__(self):
    return ('{0} is not in a supported registry.  Supported registries are '
            '{1}'.format(self.image_url, constants.ALL_SUPPORTED_REGISTRIES))


def DockerLogin(server, email, username, access_token):
  """Register the username / token for the given server on Docker's keyring."""

  # Sanitize and normalize the server input.
  parsed_url = client_lib.GetNormalizedURL(server)

  server = parsed_url.geturl()

  # 'docker login' must be used due to the change introduced in
  # https://github.com/docker/docker/pull/20107 .
  docker_args = ['login']
  if not _EmailFlagDeprecatedForDockerVersion():
    docker_args.append('--email=' + email)
  docker_args.append('--username=' + username)
  docker_args.append('--password=' + access_token)
  docker_args.append(server)  # The auth endpoint must be the last argument.

  docker_p = client_lib.GetDockerProcess(
      docker_args,
      stdin_file=sys.stdin,
      stdout_file=subprocess.PIPE,
      stderr_file=subprocess.PIPE)

  # Wait for docker to finished executing and retrieve its stdout/stderr.
  stdoutdata, stderrdata = docker_p.communicate()

  if docker_p.returncode == 0:
    # If the login was successful, print only unexpected info.
    _SurfaceUnexpectedInfo(stdoutdata, stderrdata)
  else:
    # If the login failed, print everything.
    log.error('Docker CLI operation failed:')
    log.out.Print(stdoutdata)
    log.status.Print(stderrdata)
    raise client_lib.DockerError('Docker login failed.')


def _EmailFlagDeprecatedForDockerVersion():
  """Checks to see if --email flag is deprecated.

  Returns:
    True if the installed Docker client version has deprecated the
    --email flag during 'docker login,' False otherwise.
  """
  try:
    version = client_lib.GetDockerVersion()

  except exceptions.Error:
    # Docker doesn't exist or doesn't like the modern version query format.
    # Assume that --email is not deprecated, return False.
    return False

  return version >= _EMAIL_FLAG_DEPRECATED_VERSION


def _SurfaceUnexpectedInfo(stdoutdata, stderrdata):
  """Reads docker's output and surfaces unexpected lines.

  Docker's CLI has a certain amount of chattiness, even on successes.

  Args:
    stdoutdata: The raw data output from the pipe given to Popen as stdout.
    stderrdata: The raw data output from the pipe given to Popen as stderr.
  """

  # Split the outputs by lines.
  stdout = [s.strip() for s in stdoutdata.splitlines()]
  stderr = [s.strip() for s in stderrdata.splitlines()]

  for line in stdout:
    # Swallow 'Login Succeeded' and 'saved in,' surface any other std output.
    if (line != 'Login Succeeded') and (
        'login credentials saved in' not in line):
      line = '%s%s' % (line, os.linesep)
      log.out.Print(line)  # log.out => stdout

  for line in stderr:
    if not _IsExpectedErrorLine(line):
      line = '%s%s' % (line, os.linesep)
      log.status.Print(line)  # log.status => stderr


def _CredentialHelperConfigured():
  """Returns True if a credential store is specified in the docker config.

  Returns:
    True if a credential store is specified in the docker config.
    False if the config file does not exist or does not contain a
    'credsStore' key.
  """
  try:
    # Not Using DockerConfigInfo here to be backward compatiable with
    # UpdateDockerCredentials which should still work if Docker is not installed
    path, is_new_format = client_lib.GetDockerConfigPath()
    contents = client_lib.ReadConfigurationFile(path)
    if is_new_format:
      return _CREDENTIAL_STORE_KEY in contents
    else:
      # The old format is for Docker <1.7.0.
      # Older Docker clients (<1.11.0) don't support credential helpers.
      return False
  except IOError:
    # Config file doesn't exist.
    return False


def _GCRCredHelperConfigured():
  """Returns True if docker-credential-gcr is the docker credential store.

  Returns:
    True if docker-credential-gcr is specified in the docker config.
    False if the config file does not exist, does not contain a
    'credsStore' key, or if the credstore is not docker-credential-gcr.
  """
  try:
    # Not using DockerConfigInfo here to be backward compatible with
    # UpdateDockerCredentials which should still work if Docker is not installed
    path, is_new_format = client_lib.GetDockerConfigPath()
    contents = client_lib.ReadConfigurationFile(path)
    if is_new_format and (
        _CREDENTIAL_STORE_KEY in contents):
      return contents[_CREDENTIAL_STORE_KEY] == 'gcr'
    else:
      # Docker <1.7.0 (no credential store support) or credsStore == null
      return False
  except IOError:
    # Config file doesn't exist or can't be parsed.
    return False


def ReadDockerAuthConfig():
  """Retrieve the contents of the Docker authorization entry.

  NOTE: This is public only to facilitate testing.

  Returns:
    The map of authorizations used by docker.
  """
  # Not using DockerConfigInfo here to be backward compatible with
  # UpdateDockerCredentials which should still work if Docker is not installed
  path, new_format = client_lib.GetDockerConfigPath()
  structure = client_lib.ReadConfigurationFile(path)
  if new_format:
    return structure['auths'] if 'auths' in structure else {}
  else:
    return structure


def WriteDockerAuthConfig(structure):
  """Write out a complete set of Docker authorization entries.

  This is public only to facilitate testing.

  Args:
    structure: The dict of authorization mappings to write to the
               Docker configuration file.
  """
  # Not using DockerConfigInfo here to be backward compatible with
  # UpdateDockerCredentials which should still work if Docker is not installed
  path, is_new_format = client_lib.GetDockerConfigPath()
  contents = client_lib.ReadConfigurationFile(path)
  if is_new_format:
    full_cfg = contents
    full_cfg['auths'] = structure
    file_contents = json.dumps(full_cfg, indent=2)
  else:
    file_contents = json.dumps(structure, indent=2)
  files.WriteFileAtomically(path, file_contents)


def UpdateDockerCredentials(server, refresh=True):
  """Updates the docker config to have fresh credentials.

  This reads the current contents of Docker's keyring, and extends it with
  a fresh entry for the provided 'server', based on the active gcloud
  credential.  If a credential exists for 'server' this replaces it.

  Args:
    server: The hostname of the registry for which we're freshening
       the credential.
    refresh: Whether to force a token refresh on the active credential.

  Raises:
    store.Error: There was an error loading the credentials.
  """

  # Loading credentials will ensure that we're logged in.
  # And prompt/abort to 'run gcloud auth login' otherwise.
  # Disable refreshing, since we'll do this ourself.
  cred = store.Load(prevent_refresh=True)

  if refresh:
    # Ensure our credential has a valid access token,
    # which has the full duration available.
    store.Refresh(cred)

  if not cred.access_token:
    raise exceptions.Error(
        'No access token could be obtained from the current credentials.')

  url = client_lib.GetNormalizedURL(server)
  # Strip the port, if it exists. It's OK to butcher IPv6, this is only an
  # optimization for hostnames in constants.ALL_SUPPORTED_REGISTRIES.
  hostname = url.hostname.split(':')[0]

  third_party_cred_helper = (_CredentialHelperConfigured() and
                             not _GCRCredHelperConfigured())
  if (third_party_cred_helper or
      hostname not in constants.ALL_SUPPORTED_REGISTRIES):
    # If this is a custom host or if there's a third-party credential helper...
    try:
      # Update the credentials stored by docker, passing the access token
      # as a password, and benign values as the email and username.
      DockerLogin(server, _EMAIL, _USERNAME, cred.access_token)
    except client_lib.DockerError as e:
      # Only catch docker-not-found error
      if str(e) != client_lib.DOCKER_NOT_FOUND_ERROR:
        raise

      # Fall back to the previous manual .dockercfg manipulation
      # in order to support gcloud app's docker-binaryless use case.
      _UpdateDockerConfig(server, _USERNAME, cred.access_token)
      log.warn("'docker' was not discovered on the path. Credentials have been "
               'stored, but are not guaranteed to work with the Docker client '
               ' if an external credential store is configured.')
  elif not _GCRCredHelperConfigured():
    # If this is not a custom host and no third-party cred helper...
    _UpdateDockerConfig(server, _USERNAME, cred.access_token)

  # If this is a default registry and docker-credential-gcr is configured, no-op


def _UpdateDockerConfig(server, username, access_token):
  """Register the username / token for the given server on Docker's keyring."""

  # NOTE: using "docker login" doesn't work as they're quite strict on what
  # is allowed in username/password.
  try:
    dockercfg_contents = ReadDockerAuthConfig()
  except (IOError, client_lib.InvalidDockerConfigError):
    # If the file doesn't exist, start with an empty map.
    dockercfg_contents = {}

  # Add the entry for our server.
  auth = base64.b64encode(username + ':' + access_token)

  # Sanitize and normalize the server input.
  parsed_url = client_lib.GetNormalizedURL(server)

  server = parsed_url.geturl()
  server_unqualified = parsed_url.hostname

  # Clear out any unqualified stale entry for this server
  if server_unqualified in dockercfg_contents:
    del dockercfg_contents[server_unqualified]

  dockercfg_contents[server] = {'auth': auth, 'email': _EMAIL}

  WriteDockerAuthConfig(dockercfg_contents)


def _IsExpectedErrorLine(line):
  """Returns whether or not the given line was expected from the Docker client.

  Args:
    line: The line recieved in stderr from Docker
  Returns:
    True if the line was expected, False otherwise.
  """
  expected_line_substrs = [
      # --email is deprecated
      '--email',
      # login success
      'login credentials saved in',
      # Use stdin for passwords
      'WARNING! Using --password via the CLI is insecure. Use --password-stdin.'
  ]
  for expected_line_substr in expected_line_substrs:
    if expected_line_substr in line:
      return True
  return False
