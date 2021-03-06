"""
An **intent** is a categorical representation of the User intention in a single
conversation turn. For instance, utterances like "I want a pizza", "I'd like to
order a pizza" and such, could be mapped to a single `order_pizza` intent.

Your agent will typically define a number of *intents*, representing all the
types of messages the Agent can understand and answer to. This is done by
defining :class:`Intent` sub-classes and their language resources (see
:mod:`intents.language`), and registering them to an :class:`intents.Agent`
class with :meth:`intents.Agent.register`.
"""

import re
import logging
import dataclasses
from dataclasses import dataclass, is_dataclass
from typing import List, Dict, Any, _GenericAlias

from intents.model import context, event, entity
from intents import language

logger = logging.getLogger(__name__)

#
# Intent
#

@dataclass
class IntentParameterMetadata:
    name: str
    entity_cls: entity._EntityMetaclass
    is_list: bool
    required: bool
    default: Any

class _IntentMetaclass(type):

    name: str = None
    input_contexts: List[context._ContextMetaclass] = None
    output_contexts: List[context.Context] = None
    events: List[event.Event] = None # TODO: at some point this may contain strings

    def __new__(cls, name, bases, dct):
        result_cls = super().__new__(cls, name, bases, dct)

        # Do not process Intent base class
        if name == 'Intent':
            assert not bases
            return result_cls

        if not result_cls.name:
            result_cls.name = _intent_name_from_class(result_cls)
        else:
            is_valid, reason = _is_valid_intent_name(result_cls.name)
            if not is_valid:
                raise ValueError(f"Invalid intent name '{result_cls.name}': {reason}")

        if not result_cls.input_contexts:
            result_cls.input_contexts = []
        if not result_cls.output_contexts:
            result_cls.output_contexts = []

        # TODO: check that custom parameters don't overlap Intent fields
        # TODO: check language data
        # language.intent_language_data(cls, result) # Checks that language data is existing and consistent

        events = [_system_event(result_cls.name)]
        for event_cls in result_cls.__dict__.get('events', []):
            events.append(event_cls)
        result_cls.events = events

        if not is_dataclass(result_cls):
            result_cls = dataclass(result_cls)

        # Check parameters
        result_cls.parameter_schema()

        return result_cls

class Intent(metaclass=_IntentMetaclass):
    """
    Represents a predicted intent. This is also used as a base class for the
    intent classes that model a Dialogflow Agent in Python code.

    In its simplest form, an Intent can be defined as follows:

    .. code-block:: python

        from intents import Intent

        class user_says_hello(Intent):
            \"\"\"A little docstring for my Intent\"\"\"

    *Intents* will then look for language resources in the folder where your
    Agent class is defined, and specifically in
    `language/<LANGUAGE-CODE>/user_says_hello.yaml`. More details in
    :mod:`intents.language`.

    Intents can be more complex than this, for instance:

    .. code-block:: python

        from dataclasses import dataclass
        from intents import Intent, Sys

        @dataclass
        class user_says_hello(Intent):
            \"\"\"A little docstring for my Intent\"\"\"

            user_name: Sys.Person

            name = "hello_custom_name"
            input_contexts = [a_context]
            input_contexts = [a_context(2), another_context(1)]

    This Intent has a custom name (i.e. will appear as "hello_custom_name" when
    exported to Dialogflow), will be predicted only when `a_context` is active,
    and will spawn `a_context`, lasting 2 conversation turns, and
    `another_context` lasting only 1 conversation turn.

    Most importantly, this intent has a `user_name` **parameter** of type
    :class:`Sys.Person` (check out :class:`intents.model.entity.Sys` for available system
    entities). With adequate examples in its language file, it will be able to
    match utterances like "Hello, my name is John", tagging "John" as an Entity.
    When a connector is instantiated, predictions will look like this:

    >>> predicted = connector.predict("My name is John")
    user_says_hello(user_name="John") predicted.user_name "John"
    predicted.fulfillment_text "Hi John, I'm Agent"

    Last, we notice the **@dataclass** decorator. This isn't really needed for
    the Intent to work, but adding it will have your IDE recognize the Intent
    class as a dataclass: you want autocomplete and static type checking when
    working with hundreds of intents in the same project.
    """
    # TODO: check parameter names: no underscore, no reserved names, max length

    name: str = None
    input_contexts: List[context._ContextMetaclass] = None
    output_contexts: List[context.Context] = None
    events: List[event.Event] = None # TODO: at some point this may contain strings

    # A :class:`Connector` provides this
    prediction: 'intents.Prediction'

    @property
    def confidence(self) -> float:
        return self.prediction.confidence

    @property
    def contexts(self) -> list:
        return self.prediction.contexts

    @property
    def fulfillment_text(self) -> str:
        return self.prediction.fulfillment_text

    def fulfillment_messages(
        self,
        response_group: "language.IntentResponseGroup"=language.IntentResponseGroup.RICH
    ) -> List["language.IntentResponse"]:
        """
        Return a list of fulfillment messages that are suitable for the given
        Response Group. The following scenarios may happen:

        * :class:`language.IntentResponseGroup.DEFAULT` is requested -> Message
          in the `DEFAULT` group will be returned
        * :class:`language.IntentResponseGroup.RICH` is requested

            * `RICH` messages are defined -> `RICH` messages are returned
            * No `RICH` message is defined -> `DEFAULT` messages are returned

        If present, messages in the "rich" group will be returned:

        >>> result.fulfillment_messages()
        [TextIntentResponse(choices=['I like travelling too! How can I help?']),
         QuickRepliesIntentResponse(replies=['Recommend a hotel', 'Send holiday photo', 'Where the station?'])]
         
        Alternatively, I can ask for plain-text default messages:

        >>> from intents.language import IntentResponseGroup
        >>> result.fulfillment_messages(IntentResponseGroup.DEFAULT)
        [TextIntentResponse(choices=['Nice, I can send you holiday pictures, or recommend an hotel'])]
        
        """
        if response_group == language.IntentResponseGroup.RICH and \
           not self.prediction.fulfillment_messages.get(response_group):
            response_group = language.IntentResponseGroup.DEFAULT

        return self.prediction.fulfillment_messages.get(response_group, [])

    @classmethod
    def parameter_schema(cls) -> Dict[str, IntentParameterMetadata]:
        """
        Return a dict representing the Intent parameter definition. A key is a
        parameter name, a value is a :class:`IntentParameterMetadata` object.

        TODO: consider computing this in metaclass to cache value and check types
        """
        result = {}
        for param_field in cls.__dict__['__dataclass_fields__'].values():
            # List[...]
            if isinstance(param_field.type, _GenericAlias):
                if param_field.type.__dict__.get('_name') != 'List':
                    raise ValueError(f"Invalid typing '{param_field.type}' for parameter '{param_field.name}'. Only 'List' is supported.")

                if len(param_field.type.__dict__.get('__args__')) != 1:
                    raise ValueError(f"Invalid List modifier '{param_field.type}' for parameter '{param_field.name}'. Must define exactly one inner type (e.g. 'List[Sys.Integer]')")
                
                # From here on, check the inner type (e.g. List[Sys.Integer] -> Sys.Integer)
                entity_cls = param_field.type.__dict__.get('__args__')[0]
                is_list = True
            else:
                entity_cls = param_field.type
                is_list = False

            required = True
            default = None
            if not isinstance(param_field.default, dataclasses._MISSING_TYPE):
                required = False
                default = param_field.default
            if not isinstance(param_field.default_factory, dataclasses._MISSING_TYPE):
                required = False
                default = param_field.default_factory()

            if not required and is_list and not isinstance(default, list):
                raise ValueError(f"List parameter has non-list default value in intent {cls}: {param_field}")

            result[param_field.name] = IntentParameterMetadata(
                name=param_field.name,
                entity_cls=entity_cls,
                is_list=is_list,
                required=required,
                default=default
            )

        return result

    @classmethod
    def from_prediction(cls, prediction: 'intents.Prediction') -> 'Intent':
        """
        Build an :class:`Intent` class from a :class:`Prediction`. In practice:

        #. Match parameters givent the Intent schema
        #. Instantiate the Intent
        #. Set the `prediction` field on the instantiated Intent.

        Note that this method is mostly for internal use, *connectors* will call
        it for you.
        """
        try:
            parameters = prediction.parameters(cls.parameter_schema())
        except ValueError as exc:
            raise ValueError(f"Failed to match parameters for Intent class '{cls}'. Prediction: {prediction}") from exc

        result = cls(**parameters)
        result.prediction = prediction
        return result

def _is_valid_intent_name(candidate_name):
    if re.search(r'[^a-zA-Z_\.]', candidate_name):
        return False, "must only contain letters, underscore or dot"

    if candidate_name.startswith('.') or candidate_name.startswith('_'):
        return False, "must start with a letter"

    if "__" in candidate_name:
        return False, "must not contain __"

    return True, None

def _intent_name_from_class(intent_cls: _IntentMetaclass) -> str:
    full_name = f"{intent_cls.__module__}.{intent_cls.__name__}"
    if "__" in full_name:
        logger.warning("Intent class '%s' contains repeated '_'. This is reserved: repeated underscores will be reduced to one, this may cause unexpected behavior.")
    full_name = re.sub(r"_+", "_", full_name)
    return ".".join(full_name.split(".")[-2:])

def _system_event(intent_name: str) -> str:
    """
    Generate the default event name that we associate with every intent.

    >>> _event_name('test.intent_name')
    'E_TEST_INTENT_NAME'
    """
    # TODO: This is only used in Dialogflow -> Deprecate and move to DialogflowConnector
    event_name = "E_" + intent_name.upper().replace('.', '_')
    return event.SystemEvent(event_name)
