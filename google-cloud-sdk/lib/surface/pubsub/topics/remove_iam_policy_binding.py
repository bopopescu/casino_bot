# Copyright 2017 Google Inc. All Rights Reserved.
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
"""Cloud Pub/Sub topics remove-iam-policy-binding command."""
from googlecloudsdk.api_lib.pubsub import topics
from googlecloudsdk.command_lib.iam import base_classes
from googlecloudsdk.command_lib.iam import iam_util
from googlecloudsdk.command_lib.pubsub import flags
from googlecloudsdk.command_lib.pubsub import util


class SetIamPolicy(base_classes.BaseIamCommand):
  """Removes an IAM policy binding for a Cloud Pub/Sub Topic."""

  detailed_help = iam_util.GetDetailedHelpForRemoveIamPolicyBinding(
      'topic', 'my-topic')

  @staticmethod
  def Args(parser):
    flags.AddTopicResourceArg(parser, 'to remove an IAM policy binding from.')
    iam_util.AddArgsForRemoveIamPolicyBinding(parser)

  def Run(self, args):
    client = topics.TopicsClient()
    topic_ref = util.ParseTopic(args.topic)
    return client.RemoveIamPolicyBinding(topic_ref, args.member, args.role)

