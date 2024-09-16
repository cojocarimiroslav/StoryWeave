import streamlit as st
import uuid
import rag_utils
import time

st.set_page_config(layout="wide")

st.title("Story Weawer")

# Initialization
if 'step' not in st.session_state:
    st.session_state['step'] = 1

if 'user_choice' not in st.session_state:
    st.session_state['user_choice'] = ""

if 'my_session_id' not in st.session_state:
    st.session_state['my_session_id'] = None

if 'the_story' not in st.session_state:
    st.session_state['the_story'] = ""

if 'characters_list' not in st.session_state:
    st.session_state['characters_list'] = []

if 'prompt1' not in st.session_state:
    st.session_state['prompt1'] = ""

if 'next_action_str' not in st.session_state:
    st.session_state['next_action_str'] = ""

if 'no_of_steps' not in st.session_state:
    st.session_state['no_of_steps'] = 10

if "messages" not in st.session_state:
        st.session_state.messages = []

if 'result' not in st.session_state:
    st.session_state['result'] = ""

connection = rag_utils.connect_to_postgres()
cursor = rag_utils.create_vector_extension_and_register(connection)
table_name = "story_embeddings"

placeholder = st._bottom.empty()

if st.session_state['step'] != 1:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(f'{message["content"]}', unsafe_allow_html=True)

def step_one():
    if st.session_state['my_session_id'] is None:
            st.session_state['my_session_id'] = str(uuid.uuid4())
    # Load story and characters
    the_story = rag_utils.get_story_from_blob("story.txt")
    st.session_state['the_story'] = the_story
    tries_ch_list = 0
    characters_list = st.session_state['characters_list']
    dummy_var = 0
    while len(characters_list) < 1 and tries_ch_list < 5:
        dummy_var = 1
        characters_str = rag_utils.character_extraction_chain_fun(the_story)
        characters_list = eval(characters_str)
        try:
            abilities = characters_list[0].get("character_abilities")
            weaknesses = characters_list[0].get("character_weaknesses")
            if (abilities is not None) and (weaknesses is not None):
                break
        except:
            characters_list = []
        tries_ch_list += 1
    # Display characters
    if dummy_var == 1:
        character_string = '<p style="font-size: 20px;">'
        for character in characters_list:
            character_string += f"------- Character_name: {character['character_name']} --------<br>"
            character_string += f"Abilities: {character['character_abilities']}<br>"
            character_string += f"Weaknesses: {character['character_weaknesses']}<br>"
            character_string += "-----------------------------------\n<br>"
        character_string += '</p>'
        with st.chat_message("assistant"):
            st.markdown(f'{character_string}', unsafe_allow_html=True)
        st.session_state.messages.append({"role": "assistant", "content": character_string})
    st.session_state['characters_list'] = characters_list
    prompt = placeholder.chat_input("Choose a character from the ones mentioned above: ", key="character_selection")
    if prompt is not None:
        st.session_state['prompt1'] = str(prompt)
        st.session_state['step'] = 2


if st.session_state['step'] == 1:
    step_one()
    

def step_two():
        user_choice = rag_utils.choosen_character_user(st.session_state['characters_list'], st.session_state['prompt1'])
        st.session_state['user_choice'] = user_choice
        with st.chat_message("user"):
            st.markdown(user_choice)
        st.session_state.messages.append({"role": "user", "content": user_choice})

if st.session_state['step'] == 2:
    step_two()
    st.session_state['step'] = 3

def step_three():
    # check if table exists and create one
    if not rag_utils.check_if_table_exists(cursor, table_name):
        st.session_state.messages.append({"role": "assistant", "content": "Embeddings table does not exist. Creating one."})
        with st.chat_message("assistant"):
            st.markdown(f'<p style="font-size: 20px;">Embeddings table does not exist. Creating one.</p>', unsafe_allow_html=True)
        rag_utils.create_table(cursor, connection, table_name)
        paragraphs = rag_utils.split_into_paragraphs(st.session_state['the_story'])
        for paragraph in paragraphs:
            embedding = rag_utils.get_embedding(paragraph)
            rag_utils.insert_story_embeddings(cursor, connection, table_name, embedding, paragraph)
        st.session_state.messages.append({"role": "assistant", "content": "Table created."})
        with st.chat_message("assistant"):
            st.markdown(f'<p style="font-size: 20px;">Table created.</p>', unsafe_allow_html=True)

    fragments_list = rag_utils.get_exact_match(cursor, table_name, st.session_state['user_choice'])
    # start the character chain 
    character_start_content = rag_utils.character_start_chain_memory_fun(st.session_state['user_choice'], 
                                                                         fragments_list, st.session_state['my_session_id'])
    result = rag_utils.advancing(character_start_content)
    st.session_state['result'] = result
    message = f'<p style="font-size: 20px;">{result.get("description")}<br></p>'
    action_counter = 1
    for action in result.get("actions"):
        message += f'<p style="font-size: 20px;">{action_counter}. {action}<br></p>'
        action_counter += 1
    with st.chat_message("assistant"):
        st.markdown(message, unsafe_allow_html=True)
    st.session_state.messages.append({"role": "assistant", "content": message})

if st.session_state['step'] == 3:
    step_three()
    st.session_state['step'] = 4

def step_four():
    result = st.session_state['result']
    prompt = placeholder.chat_input("Select an action from the ones mentioned above by it's number: ", key="action_selection1")
    if prompt is not None:
        prompt_int = int(prompt)
        next_action_str = rag_utils.next_actions(result.get("actions"), prompt_int)
        with st.chat_message("user"):
            st.markdown(next_action_str)
        st.session_state.messages.append({"role": "user", "content": next_action_str})
        fragments_list_exact = rag_utils.get_exact_match(cursor, table_name, st.session_state['user_choice']) 
        fragments_list_similar = rag_utils.get_similar_to_action(cursor, table_name, next_action_str)
        fragments_list_exact.extend(fragments_list_similar)

        character_advancement_content = rag_utils.character_advancement_chain_memory_fun(st.session_state['user_choice'], 
                                                                                         fragments_list_exact, 
                                                                                         st.session_state['my_session_id'], 
                                                                                         st.session_state['no_of_steps'], 
                                                                                         next_action_str)
        if st.session_state['no_of_steps'] > 0:
            result = rag_utils.advancing(character_advancement_content, True)
            st.session_state['result'] = result
            message = f'<p style="font-size: 20px;">{result.get("description")}<br></p>'
            action_counter = 1
            for action in result.get("actions"):
                message += f'<p style="font-size: 20px;">{action_counter}. {action}<br></p>'
                action_counter += 1
            st.session_state.messages.append({"role": "assistant", "content": message})
            with st.chat_message("assistant"):
                st.markdown(message, unsafe_allow_html=True)
                st.session_state['no_of_steps'] -= 1
        else:
            result = rag_utils.advancing(character_advancement_content)
            message = f'<p style="font-size: 20px;">{result.get("description")}<br></p>'
            with st.chat_message("assistant"):
                st.markdown(message, unsafe_allow_html=True)
            st.session_state.messages.append({"role": "assistant", "content": message})
            st.session_state['step'] = 5
        
if st.session_state['step'] == 4:
    step_four()

def step_five():
    st.markdown(f'<p style="font-size: 50px; text-align: center;">THE END</p>', unsafe_allow_html=True)

if st.session_state['step'] == 5:
    step_five()