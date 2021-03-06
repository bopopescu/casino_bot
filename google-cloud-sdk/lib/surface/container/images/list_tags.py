# Copyright 2016 Google Inc. All Rights Reserved.
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
"""List tags command."""

import heapq
import sys

from containerregistry.client.v2_2 import docker_http
from containerregistry.client.v2_2 import docker_image
from googlecloudsdk.api_lib.container.images import util
from googlecloudsdk.calliope import arg_parsers
from googlecloudsdk.calliope import base
from googlecloudsdk.command_lib.container import flags
from googlecloudsdk.core import exceptions
from googlecloudsdk.core import http

# Add to this as we add columns.
_DEFAULT_KINDS = [
    'BUILD_DETAILS',
    'IMAGE_BASIS',
]
# How many images to consider by default.
_DEFAULT_LIMIT = 10
# How many images for which to report vulnerabilities, by default. These are
# always the most recent images, regardless of sorting.
_DEFAULT_SHOW_OCCURRENCES_FROM = 10
# By default return the most recent timestamps.
# (The --sort-by flag uses syntax `~X` to mean "sort descending on field X.")
_DEFAULT_SORT_BY = '~timestamp'

_TAGS_FORMAT = """
    table(
        digest.slice(7:19).join(''),
        tags.list(),
        timestamp.date():optional,
        BUILD_DETAILS.buildDetails.provenance.sourceProvenance.sourceContext.context.cloudRepo.revisionId.notnull().list().slice(:8).join(''):optional:label=GIT_SHA,
        vuln_counts.list():optional:label=VULNERABILITIES,
        IMAGE_BASIS.derivedImage.sort(distance).map().extract(baseResourceUrl).slice(:1).map().list().list().split('//').slice(1:).list().split('@').slice(:1).list():optional:label=FROM,
        BUILD_DETAILS.buildDetails.provenance.id.notnull().list():optional:label=BUILD
    )
"""


class ArgumentError(exceptions.Error):
  """For missing required mutually inclusive flags."""
  pass


@base.ReleaseTracks(base.ReleaseTrack.GA, base.ReleaseTrack.BETA)
class ListTagsGAandBETA(base.ListCommand):
  """List tags and digests for the specified image."""

  detailed_help = {
      'DESCRIPTION':
          """\
          The container images list-tags command of gcloud lists metadata about
          tags and digests for the specified container image. Images must be
          hosted by the Google Container Registry.
      """,
      'EXAMPLES':
          """\
          List the tags in a specified image:

            $ {command} gcr.io/myproject/myimage

          To receive the full, JSON-formatted output (with untruncated digests):

            $ {command} gcr.io/myproject/myimage --format=json

      """,
  }

  @staticmethod
  def Args(parser):
    """Register flags for this command.

    Args:
      parser: An argparse.ArgumentParser-like object. It is mocked out in order
          to capture some information, but behaves like an ArgumentParser.
    """
    flags.AddImagePositional(parser, verb='list tags for')
    # Set flag defaults to return X most recent images instead of all.
    base.LIMIT_FLAG.SetDefault(parser, _DEFAULT_LIMIT)
    base.SORT_BY_FLAG.SetDefault(parser, _DEFAULT_SORT_BY)

    # Does nothing for us, included in base.ListCommand
    base.URI_FLAG.RemoveFromParser(parser)
    parser.display_info.AddFormat(_TAGS_FORMAT)

  def Run(self, args):
    """This is what gets called when the user runs this command.

    Args:
      args: an argparse namespace. All the arguments that were provided to this
        command invocation.

    Raises:
      InvalidImageNameError: If the user specified an invalid image name.
    Returns:
      Some value that we want to have printed later.
    """
    repository = util.ValidateRepositoryPath(args.image_name)
    http_obj = http.Http()
    with docker_image.FromRegistry(
        basic_creds=util.CredentialProvider(),
        name=repository,
        transport=http_obj) as image:
      try:
        manifests = image.manifests()
        return util.TransformManifests(
            manifests,
            repository)
      except docker_http.V2DiagnosticException as err:
        raise util.GcloudifyRecoverableV2Errors(err, {
            403: 'Access denied: {0}'.format(repository),
            404: 'Not found: {0}'.format(repository)
        })


@base.ReleaseTracks(base.ReleaseTrack.ALPHA)
class ListTagsALPHA(ListTagsGAandBETA, base.ListCommand):
  """List tags and digests for the specified image."""

  @staticmethod
  def Args(parser):
    """Register flags for this command.

    Args:
      parser: An argparse.ArgumentParser-like object. It is mocked out in order
          to capture some information, but behaves like an ArgumentParser.
    """
    # Weird syntax, but this is how to call a static base method from the
    # derived method in Python.
    super(ListTagsALPHA, ListTagsALPHA).Args(parser)
    parser.add_argument(
        '--show-occurrences',
        action='store_true',
        default=False,
        help='Whether to show summaries of the various Occurrence types.')
    parser.add_argument(
        '--occurrence-filter',
        default=' OR '.join(
            ['kind = "{kind}"'.format(kind=x) for x in _DEFAULT_KINDS]),
        help='A filter for the Occurrences which will be summarized.')
    parser.add_argument(
        '--show-occurrences-from',
        type=arg_parsers.BoundedInt(1, sys.maxint, unlimited=True),
        default=_DEFAULT_SHOW_OCCURRENCES_FROM,
        help=('How many of the most recent images for which to summarize '
              'Occurences.'))

  def Run(self, args):
    """This is what gets called when the user runs this command.

    Args:
      args: an argparse namespace. All the arguments that were provided to this
        command invocation.

    Raises:
      ArgumentError: If the user provided the flag --show-occurrences-from but
        --show-occurrences=False.
      InvalidImageNameError: If the user specified an invalid image name.
    Returns:
      Some value that we want to have printed later.
    """
    # Verify that --show-occurrences-from is set iff --show-occurrences=True.
    if args.IsSpecified('show_occurrences_from') and not args.show_occurrences:
      raise ArgumentError(
          '--show-occurrences-from may only be set if --show-occurrences=True')

    repository = util.ValidateRepositoryPath(args.image_name)
    http_obj = http.Http()
    with docker_image.FromRegistry(
        basic_creds=util.CredentialProvider(),
        name=repository,
        transport=http_obj) as image:
      try:
        manifests = image.manifests()
        # Only consider the top _DEFAULT_SHOW_OCCURRENCES_FROM images
        # to reduce computation time.
        most_recent_resource_urls = None
        if args.show_occurrences_from:
          # This block is skipped when the user provided
          # --show-occurrences-from=unlimited on the CLI.
          most_recent_resource_urls = [
              'https://%s@%s' % (args.image_name, k) for k in heapq.nlargest(
                  args.show_occurrences_from,
                  manifests,
                  key=lambda k: manifests[k]['timeCreatedMs'])]
        return util.TransformManifests(
            manifests,
            repository,
            show_occurrences=args.show_occurrences,
            occurrence_filter=args.occurrence_filter,
            resource_urls=most_recent_resource_urls)
      except docker_http.V2DiagnosticException as err:
        raise util.GcloudifyRecoverableV2Errors(err, {
            403: 'Access denied: {0}'.format(repository),
            404: 'Not found: {0}'.format(repository)
        })
