from openai import OpenAI
import psycopg2
from pgvector.psycopg2 import register_vector
import os
import json
import textwrap
import numpy as np
import uuid

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain.prompts import PromptTemplate
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import Neo4jChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

# constants
KEY_VAULT_URL = os.environ.get("KEY_VAULT_URL")
AZURE_STORAGE_BLOB_URL = os.environ.get("AZURE_STORAGE_BLOB_URL")
AZURE_STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME")
POSTGRESQL_USERNAME = os.environ.get("POSTGRESQL_USERNAME")
POSTGRESQL_HOST = os.environ.get("POSTGRESQL_HOST")
POSTGRESQL_DATABASE = os.environ.get("POSTGRESQL_DATABASE")
NEO4J_URL = os.environ.get("NEO4J_URL")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME")

# secrets retrieval
credential = DefaultAzureCredential()

def get_secret(secret_name:str, credential)-> str:
    """Get secret from Azure Key Vault

    Args:
        secret_name (str): the name of the secret
        credential (_type_): the credential to access the key vault

    Returns:
        str: the value of the secret
    """
    secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    retrieved_secret = secret_client.get_secret(secret_name)
    return retrieved_secret.value
##################

# story utils
def get_story_from_blob(blob_name:str, 
                        account_url:str = AZURE_STORAGE_BLOB_URL, 
                        credential = credential, 
                        container_name:str = AZURE_STORAGE_CONTAINER_NAME, 
                        )-> str:
    """Get the story from the Azure Blob Storage

    Args:
        account_url (str): the url of the account
        credential (_type_): the credential to access the blob storage
        container_name (str): the name of the container
        blob_name (str): the name of the blob

    Returns:
        str: the story
    """
    blob_service_client = BlobServiceClient(account_url, credential=credential)
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)
    the_story = blob_client.download_blob().readall() # read blob content as string
    return the_story.decode("utf-8")


# neo4j utils
graph = Neo4jGraph(NEO4J_URL, NEO4J_USERNAME,
                    get_secret("NEO4J-PASSWORD", credential))

# postresql utils
def connect_to_postgres(username:str = POSTGRESQL_USERNAME, 
                        password:str = get_secret("POSTGRESQL-PASSWORD", credential),
                        host:str = POSTGRESQL_HOST, 
                        database:str = POSTGRESQL_DATABASE):
    """Connect to the PostgreSQL database

    Args:
        username (str): the username to access the database
        password (str): the password to access the database
        host (str): the host of the database
        database (str): the name of the database

    Returns:
        _type_: the connection to the database
    """
    conn = psycopg2.connect(
    host = host,
    database = database,
    user = username,
    password = password)
    return conn

def create_vector_extension_and_register(conn):
    """Create the vector extension and register it

    Args:
        conn (_type_): the connection to the database

    Returns:
        _type_: the cursor to the database
    """
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return cur

def create_table(cur, conn, table_name:str):
    """Create table & columns

    Args:
        cur (_type_): cursor
        conn (_type_): connection
        table_name (str): name of the table
    """
    try:
        cur.execute(f"DROP TABLE IF EXISTS {table_name};")
        column_names = ["story_text TEXT", "story_embeddings VECTOR"]
        column_names = ", ".join(column_names)
        cur.execute(f'CREATE TABLE {table_name} ({column_names});')
        conn.commit()
        print(f"Table {table_name} created successfully")
        return
    except:
        conn.rollback()

def check_if_table_exists(cur, table_name:str):
    """Check if the table exists

    Args:
        cur (_type_): cursor
        table_name (str): name of the table

    Returns:
        bool: True if the table exists, False otherwise
    """
    cur.execute(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}');")
    return cur.fetchone()[0]

def insert_story_embeddings(cur, conn, table_name:str, 
                            story_embedding, story_text:str):
    """Insert story embeddings into the table

    Args:
        cur (_type_): cursor
        conn (_type_): connection
        table_name (str): name of the table
        story_embedding (_type_): story embeddings
        story_text (str): story text
    """
    sql_comand = f'INSERT INTO {table_name} (story_text, story_embeddings) VALUES (%s, %s);'
    args1 = (story_text, story_embedding)    
    cur.execute(sql_comand, args1)
    conn.commit()

def count_a_in_b(text_to_search:str, text_to_search_in:str)-> int:
    """Count the number of occurences of a text in another text

    Args:
        text_to_search (str): the text to search
        text_to_search_in (str): the text to search in

    Returns:
        int: the number of occurences
    """
    text_to_search = text_to_search.lower()
    text_to_search_in = text_to_search_in.lower()
    return text_to_search_in.count(text_to_search)

def get_exact_match(cur, table_name:str, 
                    character_name:str, 
                    top_k:int = 3) -> list:
    """Get the exact match of a character name in the story

    Args:
        cur (_type_): cursor
        table_name (str): name of the table
        character_name (str): character name
        top_k (int, optional): number of results. Defaults to 3.

    Returns:
        list: list of paragraphs
    """
    sql_command = f'SELECT story_text FROM {table_name} WHERE story_text ILIKE %s'
    cur.execute(sql_command, (f'%{character_name}%',))
    result = cur.fetchall()
    final_result = [(paragraph[0], count_a_in_b(character_name, paragraph[0])) for paragraph in result]
    final_result.sort(key=lambda x: x[1], reverse = True)
    first_results = final_result[:top_k]
    first_string_results = [paragraph[0] for paragraph in first_results]
    return first_string_results

def get_similar(cur, table_name:str, vector, k:int=5) -> list:
    """Get the similar paragraphs to a given vector

    Args:
        cur (_type_): cursor
        table_name (str): name of the table
        vector (_type_): vector
        k (int, optional): number of results. Defaults to 5.

    Returns:
        list: list of paragraphs
    """
    cur.execute(f'SELECT story_text, (story_embeddings <=> %s) as similarity_score FROM {table_name} ORDER BY story_embeddings <=> %s LIMIT {k}', (vector, vector))
    result = cur.fetchall()
    final_result = [x[0] for x in result]
    return final_result

# openai utils

client = OpenAI(api_key=get_secret("OPENAI-API-KEY", credential))

llm = ChatOpenAI(
    openai_api_key=get_secret("OPENAI-API-KEY", credential),
    model="gpt-4o-mini-2024-07-18",
    temperature=0.1,
    max_tokens=500,
)

# templates
# TEMPLATE 1 --------------------------------------------------------------
character_extraction_template = PromptTemplate.from_template("""
You are a talented expert in text understanding. 

Please extract a list with characters from the story, with their names, abilities and weaknesses as a list of dictionaries with the structure 
                                              {{"character_name": "", "character_abilites":"", "character_weaknesses":""}}. 
                                                             Do not return the response type. ONLY the response with the structure stated above, in plain text.

Story: {story}
Characters: 
""")
# ----------------------------------------------------------------------------


# --------------------------TEMPLATE 2
character_starting_point_system_message = """
I want you to act as if you are a classic text adventure game and we are playing. 
I don’t want you to ever break out of your character, and you must not refer to yourself in any way.
Based on this list of fragments from the story {fragment_list} generate a short (maximum 10 sentences) description of your surroundings and things you have on you as a monologue and 5 options of what actions you could do
following the structure {{"description": "", "actions": []}}. Respond using ONLY the response structure in plain text.

Choose the surroundings randomly from the list of fragments provided.

"""


character_starting_point_question = """
You are {choosen_character}. 

Answer:
"""

# supply choosen_character and story
character_starting_point_memory_prompt = ChatPromptTemplate([
    ("system", character_starting_point_system_message),
    MessagesPlaceholder(variable_name = "history"),
    ("human", character_starting_point_question)
])
# ---------------------------------------------------

# -----------------------------TEMPLATE 3
character_advancement_system_message = """
I want you to act as if you are a classic text adventure game and we are playing. 
I don’t want you to ever break out of your character, and you must not refer to yourself in any way.

You have only {no_of_steps} steps left to win or lose the game, make it count!

Based on this list of fragments from the story {fragment_list} and selected action generate a short (maximum 5 sentences) description of the action, how the action took place and 
5 options of what actions you could do following the structure {{"description": "", "actions": []}} in plain text.

Rules -------------------------
Please formulate the description of the action taking into considerations the following rules:
1. The description should contain the entire action with a development and an ending;
2. The description should be consistent with the fragments from the story and the messages history;
3. The description should be at the first person present tense.

Please formulate the next actions options taking into consideration the following rules:
1. The actions should be mystical or adventurous.
2. Take into consideration previous actions and the story to have a consistent storyline;
3. The actions should be ENGAGING and should have an unexpected twist;
4. The actions should be consistent with the character's abilities and weaknesses.
5. The actions should be consistent with the character's surroundings and the story.
6. The actions should be different from each other and should have different outcomes. 
7. The actions should lead to a development and a conclusion of the story and should be engaging for the user.
8. The actions should take MORE THAN ONE DAY to complete!
9. The actions should lead to an end of the story, with a conclusion that would end the storyline.
10. The actions SHOULD NOT be boring or lame!
11. The actions should be active and should involve the character in a direct way.

Rules for specific steps:
- If you have 1 steps left, please provide a final action that will end the game;
- If you have 0 steps left, please provide ONLY an ending description that would end the storyline, without actions.

------------------------------

Formulate the answer using only ONLY the response structure. You should sacredly follow the above rules! 
"""

character_advancement_question = """
You are {choosen_character}. You choose {choosen_action}.

Answer:
"""
character_advancement_memory_prompt = ChatPromptTemplate([
    ("system", character_advancement_system_message),
    MessagesPlaceholder(variable_name = "history"),
    ("human", character_advancement_question)
])

def character_extraction_chain_fun(the_story,
                                   character_extraction_template = character_extraction_template, 
                                   llm = llm):
    """Extract characters from the story

    Args:
        character_extraction_template (_type_): extraction of the characters from the story
        llm (_type_): llm model
        the_story (_type_): the story to extract the characters from

    Returns:
        _type_: name of the characters, their abilities and weaknesses
    """
    characters_chain = character_extraction_template | llm
    characters_response = characters_chain.invoke({"story": the_story})
    characters_content = characters_response.content
    return characters_content

def character_start_chain_memory_fun(choosen_character, fragment_list, session_id, 
                                     character_starting_point_memory_prompt = character_starting_point_memory_prompt, 
                                     llm = llm):
    """Start the character chain
    Args:
        character_starting_point_memory_prompt (_type_): supply choosen_character and story
        llm (_type_): llm model
        choosen_character (_type_): the character that is choosen by the user
        the_story (_type_): the story from above
        session_id (_type_): id of the session

    Returns:
        _type_: description of the surroundings and actions that user can perform
    """
    character_start_chain = character_starting_point_memory_prompt | llm
    chat_with_message_history = RunnableWithMessageHistory(character_start_chain, get_memory, input_messages_key="choosen_character", history_messages_key="history")
    character_start_response = chat_with_message_history.invoke({"choosen_character": choosen_character, "fragment_list": fragment_list}, config = {"configurable": {"session_id": session_id}})
    character_start_content = character_start_response.content
    return character_start_content


def character_advancement_chain_memory_fun(choosen_character, fragment_list, session_id, 
                                           no_of_steps, choosen_action, 
                                           character_advancement_memory_prompt = character_advancement_memory_prompt, 
                                           llm = llm):
    """Character advancement chain
    Args:
        character_advancement_memory_prompt (_type_): chain with the next action options & nr of steps
        llm (_type_): llm model
        choosen_character (_type_): the character that is choosen by the user
        the_story (_type_): the story from above
        session_id (_type_): id of the session
        no_of_steps (_type_): number of steps that the user has until the game ends
        choosen_action (_type_): the action that the user has choosen

    Returns:
        _type_: description of the action and the next action options
    """
    character_advancement_chain = character_advancement_memory_prompt | llm
    chat_with_message_history = RunnableWithMessageHistory(character_advancement_chain, get_memory, input_messages_key="choosen_action", history_messages_key="history")
    character_advancement_response = chat_with_message_history.invoke({"choosen_character": choosen_character, "fragment_list": fragment_list,
                                                                       "no_of_steps": no_of_steps, "choosen_action": choosen_action}, 
                                                                       config = {"configurable": {"session_id": session_id}})
    character_advancement_content = character_advancement_response.content
    return character_advancement_content

def get_memory(session_id):
    """Get the memory for the session

    Args:
        session_id (_type_): id of the session

    Returns:
        _type_: memory for the session
    """
    return Neo4jChatMessageHistory(session_id=session_id, graph=graph)

# for game
def choosen_character_user(characters_list, user_choice):
    """The user choice of the character 

    Args:
        characters_list (_type_): list of characters in the story

    Returns:
        _type_: the name of the choosen character
    """
    for character in characters_list:
        if character['character_name'].lower() == user_choice.lower():
            return character['character_name']
    return None

def advancing(character_start_content, last_step = False):
    """The user choice of the character 

    Args:
        character_start_content (_type_): the content of the character start chain

    Returns:
        _type_: the description of the surroundings and the actions that the user can perform
    """
    data = json.loads(character_start_content)
    description = data.get("description")
    actions = data.get("actions")
    result = {"description": description, "actions": actions}
    return result

def next_actions(actions, a_no):
    """Future actions of the user 

    Args:
        result (_type_): the next actions that the user can perform
    Returns:
        _type_: the next choosen action
    """
    while True:
        try:
            # Check if the choice is valid
            if 1 <= a_no <= len(actions):
                chosen_action = actions[a_no-1]
                return chosen_action
            else:
                print("Invalid choice. Please enter a number within the given choices.")
        
        except ValueError:
            print("Invalid input. Please enter a number: ")

    return chosen_action

def get_embedding(text, client= client, 
                  model="text-embedding-3-small"):
   """_summary_

   Args:
       text (str): the text that the user input
       model (str, optional): Defaults to "text-embedding-3-small".

   Returns:
       _type_: embeddings
   """
   return client.embeddings.create(input = [text], model=model).data[0].embedding

def split_into_paragraphs(text):
    """Split the story into paragraphs

    Args:
        text (_type_): the text that the user input, in this case a story

    Returns:
        _type_: splitted parapraphs 
    """
    paragraphs = text.split("\n")
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    return paragraphs

def get_similar_to_action(cur, table_name, text, client = client):
    action_embedding = np.array(get_embedding(text, client))
    action_similar = get_similar(cur, table_name, action_embedding)
    return action_similar

