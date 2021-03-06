"""
**Intent** language files have the following structure:

.. code-block:: yaml

    examples:
      - an example utterance
      - another example utterance
      - an example utterance with $foo{42} as a numeric parameter

    slot_filling_prompts:
      foo:
        - Tell me the value for "foo"

    responses:
      default:
        - text:
          - A plain text response
          - An alternative response
          - Another alternative, referencing $foo as a paramneter
      rich:
        - text:
          - A text response for rich clients
        - quick_replies:
          - a reply chip
          - another reply chip

Let's look at the sections of this file.

* **examples** contain example utterances that will be used to predict the given
  intent. If your intent has a parameter, it can be referenced as
  $parameter_name{example value}. You can omit this section if your intent is
  not meant to be predicted (some intents are trigger-only)
* **slot_filling_prompt** are used when your intent has a mandatory parameter,
  and this parameter could not be matched in the user message. These prompts
  will be used to ask the User about that parameter. You can omit this section
  if your intent has no mandatory parameters, or if you don't want to define
  custom prompts.
* **responses** contain messages that Agent will send to User in response to the
  Intent. Two response groups are available:

  * **default** can only contain plain-text messages. It is good practice to
    always provide text-only response for situations where rich ones can't be rendered,
    such as vocal assistants, smartphone notifications and such. The `text`
    response type is specified in :class:`TextIntentResponse`
  * **rich** responses allow some extra types:
    :class:`QuickRepliesIntentResponse`, :class:`ImageIntentResponse`,
    :class:`CardIntentResponse` and :class:`CustomPayloadIntentResponse`

"""

import os
import re
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Union

import yaml

from intents.language import agent_language, LanguageCode
from intents.model.entity import _EntityMetaclass

#
# Example Utterances
#

class UtteranceChunk:
    """
    An Example Utterance can be seen as a sequence of Chunks, where each Chunk
    is either a mapped Entity, or a plain text string.
    """

@dataclass
class TextUtteranceChunk(UtteranceChunk):
    """
    An Utterance Chunk that is a static, plain text string.
    """
    text: str

@dataclass
class EntityUtteranceChunk(UtteranceChunk):
    """
    An Utterance Chunk that is a matched entity
    """
    entity_cls: _EntityMetaclass
    parameter_name: str
    parameter_value: str

# TODO: check that parameter_value is one of the entries in custom entities
RE_EXAMPLE_PARAMETERS = re.compile(r"\$(?P<parameter_name>[\w]+)\{(?P<parameter_value>[^\}]+)\}")

class ExampleUtterance(str):
    """
    One of the example Utterances of a given Intent.
    """
    
    # TODO: check for escape characters - intent is possibly intent_cls
    def __init__(self, example: str, intent: "intents.Intent"):
        self._intent = intent
        self.chunks() # Will check parameters
    
    def __new__(cls, example: str, intent: "intents.Intent"):
        return super().__new__(cls, example)

    def chunks(self):
        """
        Return the Utterance as a sequence of :class:`UtteranceChunk`. Each
        chunk is either a plain text string, or a mapped Entity.

        >>> utterance = ExampleUtterance("My name is $user_name{Guido}!", intents.user_gives_name)
        >>> utterance.chunks()
        [
            TextUtteranceChunk(text="My name is "),
            EntityUtteranceChunk(entity_cls=Sys.Person, parameter_name="user_name", parameter_value="Guido"),
            TextUtteranceChunk(text="!")
        ]

        TODO: handle escaping
        """
        parameter_schema = self._intent.parameter_schema()
        result = []
        last_end = 0
        for m in RE_EXAMPLE_PARAMETERS.finditer(self):
            m_start, m_end = m.span()
            m_groups = m.groupdict()
            if m_start > 0:
                result.append(TextUtteranceChunk(text=self[last_end:m_start]))
            
            if (parameter_name := m_groups['parameter_name']) not in parameter_schema:
                raise ValueError(f"Example '{self}' references parameter ${parameter_name}, but intent {self._intent.name} does not define such parameter.")
 
            entity_cls = parameter_schema[parameter_name].entity_cls
            result.append(EntityUtteranceChunk(
                entity_cls=entity_cls,
                parameter_name=m_groups['parameter_name'],
                parameter_value=m_groups['parameter_value']
            ))

            last_end = m_end

        last_chunk = TextUtteranceChunk(text=self[last_end:])
        if last_chunk.text:
            result.append(last_chunk)

        return result

#
# Responses
#

class IntentResponseGroup(Enum):
    """
    Intent responses are divided in groups. The same intent can be answered with
    a set of plain-text responses (:const:`IntentResponseGroup.DEFAULT`), or with
    rich content (:const:`IntentResponseGroup.RICH`) that includes cards, images
    and quick replies.
    """
    DEFAULT = "default"
    RICH = "rich"

class IntentResponse:
    """
    One of the Response Utterances of a given Intent.
    """

    @classmethod
    def from_yaml(cls, data: dict):
        """
        Instantiate an IntentResponse from language data, as it's found in its
        YAML file. Typically, IntentResponse is a dataclass and `data` is a dict
        of fields; however specific subclasses may override with custom
        parameters.
        """
        return cls(**data)

@dataclass(frozen=True)
class TextIntentResponse(IntentResponse):
    """
    A plain text response. The actual response is picked randomly from a pool of
    choices.

    In the YAML definition a text response can either be a string, as in

    .. code-block:: yaml

        responses:
          default:
            - text: This is a response

    Or a list of choices (the output fulfillment message will be chosen
    randomly among the different options)

    .. code-block:: yaml

        responses:
          default:
            - text:
              - This is a response
              - This is an alternative response
    """

    choices: List[str]

    @classmethod
    def from_yaml(cls, data: Union[str, List[str]]):
        if isinstance(data, str):
            return cls([data])

        assert isinstance(data, list)
        return cls(data)

@dataclass(frozen=True)
class QuickRepliesIntentResponse(IntentResponse):
    """
    A set of Quick Replies that can be used to answer the Intent. Each reply
    must be shorter than 20 characters.

    In the YAML definition a quick replies response can either be a string, as in

    .. code-block:: yaml

        rich:
          - quick_replies: Order Pizza

    Or a list of replies, that will be rendered as separate chips

    .. code-block:: yaml

        rich:
          - quick_replies:
            - Order Pizza
            - Order Beer
    """

    replies: List[str]

    def __post_init__(self):
        for rep in self.replies:
            if len(rep) > 20:
                raise ValueError(f"Quick Replies must be shorter than 20 chars. Quick reply '{rep}' is {len(rep)} chars long.")

    @classmethod
    def from_yaml(cls, data: Union[str, List[str]]):
        if isinstance(data, str):
            return cls([data])
        
        assert isinstance(data, list)
        return cls(data)

@dataclass(frozen=True)
class ImageIntentResponse(IntentResponse):
    """
    A simple image, defined by its URL and an optional title

    In the YAML definition an image response can either be a string with the
    image URL, as in

    .. code-block:: yaml

        rich:
          - image: https://example.com/image.png

    Or an object with the image URL and a title, as in

    .. code-block:: yaml

        rich:
          - image:
              url: https://example.com/image.png
              title: An example image
    """
    url: str
    title: str = None

    @classmethod
    def from_yaml(cls, data: Union[str, List[str]]):
        if isinstance(data, str):
            return cls(url=data)
        
        assert isinstance(data, dict)
        return cls(**data)

@dataclass(frozen=True)
class CardIntentResponse(IntentResponse):
    """
    A simple content card that can be rendered on many platforms.

    In the YAML, this is defined as

    Or an object with the image URL and a title, as in

    .. code-block:: yaml

        rich:
          - card:
              title: The card title
              subtitle: An optional subtitle
              image: https://example.com/image.jpeg
              link: https://example.com/
    """
    title: str
    subtitle: str = None
    image: str = None
    link: str = None

@dataclass(frozen=True)
class CustomPayloadIntentResponse(IntentResponse):
    """
    Custom Payloads are objects with arbitrary fields, they are supported by
    Dialogflow in every response group, including "Default". Currently they can
    only be defined in the YAML as free form payloads; support for marshalling
    or generation from code is expected in future developments.
    """

    name: str
    payload: dict

    @classmethod
    def from_yaml(cls, data: Dict[str, dict]):
        """
        In the YAML definition a custom payload is defined as follows

        .. code-block:: yaml

            rich:
              - custom:
                  custom_location:
                    latitude: 45.484907
                    longitude: 9.203299
                    name: Piazza Duca D'Aosta, Milano

        NOTE: while not currently enforced, consistency is expected between
        payload names and their fields. Future versions of the library will
        marshal custom payloads against dataclass schemas.
        """
        if not isinstance(data, dict):
            raise ValueError(f"A custom payload is expected to be a dict in the form 'payload_name: {{\"foo\": \"bar\"}}. Found: {data}")
        if len(data) != 1:
            raise ValueError(f"A custom payload is expected to contain a single key representing the payload name, mapping to its value (e.g. 'location: {{\"latitude\": 42, ...}}'). Found {len(data)} keys: {data.keys()}")

        payload_name = list(data.keys())[0]
        payload_content = list(data.values())[0]

        if not isinstance(payload_content, dict):
            raise ValueError(f"Custom payloads are expected to be dictionaries. {payload_name} has value: {payload_content}")

        return CustomPayloadIntentResponse(payload_name, payload_content)

@dataclass
class IntentLanguageData:
    """
    Language data for an Intent consists of three resources:

    * Example Utterances
    * Slot Filling Prompts
    * Responses

    **Example Utterances** are the messages that Agent will be trained on to
    recognize the Intent.

    **Responses**, intuitively, are the Agent's response messages that will be sent
    to User once the Intent is recognized. They are divided in groups: a
    :const:`IntentResponseGroup.DEFAULT` group (mandatory) can only contain plain
    text responses. A :const:`IntentResponseGroup.RICH` group can provide intent
    responses that include cards, images and quick replies.

    **Slot Filling Promps** are used to solve parameters that couldn't be tagged in
    the original message. For instance a `order_pizza` intent may have a
    `pizza_type` parameter. When User asks "I'd like a pizza" we want to fill
    the slot by asking "What type of pizza?". `slot_filling_prompts` will map
    parameters to their prompts: `{"pizza_type": ["What type of pizza?"]}`
    """
    example_utterances: List[ExampleUtterance]
    slot_filling_prompts: Dict[str, List[str]]
    responses: Dict[IntentResponseGroup, List[IntentResponse]]

#
# Language Data Loader
#

def intent_language_data(
    agent_cls: "intents.model.agent._AgentMetaclass",
    intent_cls: "intents.model.intent._IntentMetaclass",
    language_code: LanguageCode=None
) -> Dict[LanguageCode, IntentLanguageData]:
    try:
        language_folder = agent_language.agent_language_folder(agent_cls)

        if not language_code:
            result = {}
            for language_code in agent_cls.languages:
                language_data = intent_language_data(agent_cls, intent_cls, language_code)
                result[language_code] = language_data[language_code]
            return result

        if isinstance(language_code, str):
            language_code = LanguageCode(language_code)

        language_file = os.path.join(language_folder, language_code.value, f"{intent_cls.name}.yaml")
        if not os.path.isfile(language_file):
            raise ValueError(f"Language file not found for intent '{intent_cls.name}'. Expected path: {language_file}. Language files are required even if the intent doesn't need language; in this case, use an empty file.")
        
        with open(language_file, 'r') as f:
            language_data = yaml.load(f.read(), Loader=yaml.FullLoader)

        if not language_data:
            return IntentLanguageData([], {}, [])

        examples_data = language_data.get('examples', [])
        responses_data = language_data.get('responses', [])

        examples = [ExampleUtterance(s, intent_cls) for s in examples_data]
        responses = _build_responses(responses_data)
        
        language_data = IntentLanguageData(
            example_utterances=examples,
            slot_filling_prompts=language_data.get('slot_filling_prompts', {}),
            responses=responses
        )

        return {language_code: language_data}
    except Exception as e:
        raise RuntimeError(f"Failed to load language data for intent {intent_cls.name} (see stacktrace above for root cause).") from e


def _build_responses(responses_data: dict):
    result = {}

    response_group: str
    responses: List[dict]
    for response_group, responses in responses_data.items():
        try:
            response_group = IntentResponseGroup(response_group)
        except ValueError as exc:
            raise NotImplementedError(f"Unsupported Response Group '{response_group}' in 'responses'. Currently, only 'default' and 'rich' are supported") from exc

        result[response_group] = []
        for r in responses:
            assert len(r) == 1
            for r_type, r_data in r.items():
                if response_group == IntentResponseGroup.DEFAULT and r_type != 'text':
                    raise ValueError(f"Message type {r_type} found in response group 'default'. Only 'text' type is allowed in 'default': please define the additional 'rich' response group to use rich responses.")

                if r_type == 'text':
                    result[response_group].append(TextIntentResponse.from_yaml(r_data))
                elif r_type == 'quick_replies':
                    result[response_group].append(QuickRepliesIntentResponse.from_yaml(r_data))
                elif r_type == 'image':
                    result[response_group].append(ImageIntentResponse.from_yaml(r_data))
                elif r_type == 'card':
                    result[response_group].append(CardIntentResponse.from_yaml(r_data))
                elif r_type == 'custom':
                    result[response_group].append(CustomPayloadIntentResponse.from_yaml(r_data))
                else:
                    raise NotImplementedError(f"Unsupported response type '{r_type}'. Currently, only 'text' is supported")
                
    return result
