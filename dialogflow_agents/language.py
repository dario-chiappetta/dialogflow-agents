"""
Utilities to manage the Agent's language resources. An Agent is defined as a
Python package. The package is expected to have a `language` folder at its top
level, containing language resources for intents and entities, in the for of
YAML files.

TODO: expand
"""
import os
import re
import sys
import logging
from typing import List
from dataclasses import dataclass

import yaml

import dialogflow_agents
from dialogflow_agents.model.intent import IntentMetaclass
from dialogflow_agents.dialogflow_format import agent_definition as df

logger = logging.getLogger(__name__)

RE_EXAMPLE_PARAMETERS = re.compile(r"\$(?P<parameter_name>[\w]+)\{(?P<parameter_value>[^\}]+)\}")

class ExampleUtterance(str):
    
    # TODO: init with intent and check for escape characters
    def __init__(self, example: str, intent: dialogflow_agents.Intent):
        self._intent = intent
        self.df_chunks() # Will check parameters
    
    def __new__(cls, example: str, intent: dialogflow_agents.Intent):
        return super().__new__(cls, example)

    def df_chunks(self) -> List[df.UsersaysChunk]:
        """
        Return Chunks in the Dialogflow intent definition format.
        """
        result = []
        last_end = 0
        for m in RE_EXAMPLE_PARAMETERS.finditer(self):
            m_start, m_end = m.span()
            m_groups = m.groupdict()
            if m_start > 0:
                result.append(df.UsersaysTextChunk(text=self[last_end:m_start], userDefined=True))
            
            if (parameter_name := m_groups['parameter_name']) not in self._intent.__dataclass_fields__:
                raise ValueError(f"Example '{self}' references parameter ${parameter_name}, but intent {self._intent.metadata.name} does not define such parameter.")
 
            meta = f'@{self._intent.__dataclass_fields__[parameter_name].type.df_entity.name}'
            result.append(df.UsersaysEntityChunk(
                text=m_groups['parameter_value'],
                alias=m_groups['parameter_name'],
                meta=meta,
                userDefined=True
            ))
            last_end = m_end
            
        last_chunk = df.UsersaysTextChunk(text=self[last_end:], userDefined=True)
        if last_chunk.text:
            result.append(last_chunk)

        return result

class ResponseUtterance(str):
    pass

def intent_language_data(agent_cls: type, intent: IntentMetaclass) -> (List[ExampleUtterance], List[ResponseUtterance]):
    main_agent_package = agent_cls.__module__.split('.')[0]
    agent_folder = sys.modules[main_agent_package].__path__[0]
    language_folder = os.path.join(agent_folder, 'language')
    if not os.path.isdir(language_folder):
        raise ValueError(f"No language folder found for agent {agent_cls} (expected: {language_folder})")
    
    # TODO: support multiple languages
    language_file = os.path.join(language_folder, "intents", f"{intent.metadata.name}__en.yaml")
    if not os.path.isfile(language_file):
        raise ValueError(f"Language file not found for intent '{intent.metadata.name}'. Expected path: {language_file}. Language files are required even if the intent doesn't need language; in this case, use an empty file.")
    
    with open(language_file, 'r') as f:
        language_data = yaml.load(f.read(), Loader=yaml.FullLoader)

    if not language_data:
        return [], []

    examples_data = language_data.get('examples', [])
    responses_data = language_data.get('responses', [])

    examples = [ExampleUtterance(s, intent) for s in examples_data]

    return examples, responses_data

# from example_agent import ExampleAgent
# from example_agent.intents import smalltalk

# examples, responses = intent_language_data(ExampleAgent, smalltalk.user_name_give)
# for e in examples:
#     print(e.df_chunks())
