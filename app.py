from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import os
import random
import uuid
import time
from PIL import Image, ImageDraw, ImageFont # Import Pillow modules
import io # To handle image data in memory

app = Flask(__name__)
# !!! IMPORTANT: Change this secret key for production !!!
app.config['SECRET_KEY'] = 'a_very_secret_key_replace_me_in_prod'

# --- Configuration ---
WRITING_TIME_SECONDS = 30
VOTING_TIME_SECONDS = 30
WAIT_PAGE_REFRESH_SECONDS = 1

# --- Font and Rendering Configuration (Percentage-based, adjust values 0-100) ---
# Paths are relative to app root's static folder
TITLE_FONT_PATH = 'static/fonts/title.otf'
BODY_FONT_PATH = 'static/fonts/body.ttf'

# Text 1 (Title) - Percentages of actual image dimensions
TITLE_TOP_PERCENT = 10         # Top edge starts 10% down from image top
TITLE_WIDTH_PERCENT = 75       # Bounding box is 75% of image width (Adjust as needed)
TITLE_FONT_SIZE_PERCENT_OF_HEIGHT = 8 # Font size is 8% of image height (Adjust as needed)

# Text 2 (Body) - Percentages of actual image dimensions
BODY_TOP_PERCENT = 80          # Top edge starts 80% down from image top (Adjust as needed)
BODY_WIDTH_PERCENT = 80        # Bounding box is 80% of image width (Adjust as needed)
BODY_FONT_SIZE_PERCENT_OF_HEIGHT = 3  # Font size is 3% of image height (Adjust as needed)
BODY_LINE_HEIGHT_MULTIPLIER = 1.2 # Vertical space between lines = font size * multiplier (Adjust as needed)


# --- Game State ---
game_state = {
    'players': {},
    'state': 'lobby',
    'current_round': 0,
    'current_poster': None, # Path relative to static/
    'captions': {}, # {session_id: {'text1': 'Caption 1 Text', 'text2': 'Caption 2 Text'}}
    'votes': {},
    'all_posters': [],
    'posters_used': [],
    'winning_caption_id': None,
    'phase_end_time': None
}

# --- Helper Functions ---

def get_player_id():
    """Gets the unique ID for the current player's session."""
    if 'player_id' not in session:
        session['player_id'] = str(uuid.uuid4())
        # print(f"Generated new session ID: {session['player_id']}") # Keep commented
    return session['player_id']

def get_current_player():
    """Returns the player data for the current session from game_state, or None."""
    player_id = get_player_id()
    return game_state['players'].get(player_id)

def load_all_posters():
    """Loads poster paths from the static directory."""
    if not app.static_folder:
         print("Warning: app.static_folder is not set or not available.")
         return []

    poster_dir = os.path.join(app.static_folder, 'posters')
    # print(f"DEBUG: Looking for posters in directory: {poster_dir}") # Keep commented
    if not os.path.exists(poster_dir):
        print(f"DEBUG: Warning: Posters directory not found at {poster_dir}")
        return []
    try:
        poster_files = [f for f in os.listdir(poster_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))]
        poster_paths = [f'posters/{f}' for f in poster_files]
        # print(f"DEBUG: Loaded {len(poster_paths)} poster paths.") # Keep commented
        return poster_paths
    except Exception as e:
        print(f"DEBUG: Error listing directory {poster_dir}: {e}")
        return []

def reset_round_state():
    """Clears state for a new round."""
    game_state['current_poster'] = None
    game_state['captions'] = {}
    game_state['votes'] = {}
    game_state['winning_caption_id'] = None
    for player_data in game_state['players'].values():
        player_data['submitted_this_round'] = False
        player_data['voted_this_round'] = False
    game_state['phase_end_time'] = None

def start_new_round():
    """Selects a new poster and sets the state to writing."""
    reset_round_state()

    available_posters = [p for p in game_state['all_posters'] if p not in game_state['posters_used']]

    if not available_posters:
        if game_state['all_posters']:
            game_state['posters_used'] = [] # Reuse if needed
            available_posters = game_state['all_posters']
            print("Warning: All posters used in previous games. Reusing posters.")
        else:
            print("Error: No posters loaded at all! Cannot start round.")
            game_state['state'] = 'game_over' # Should not happen if before_request works
            return False

    selected_poster = random.choice(available_posters)
    game_state['current_poster'] = selected_poster
    game_state['posters_used'].append(selected_poster)

    game_state['current_round'] += 1
    game_state['state'] = 'writing'
    game_state['phase_end_time'] = time.time() + WRITING_TIME_SECONDS # Set timer for writing
    print(f"Starting Round {game_state['current_round']} with poster: {game_state['current_poster']}. Writing timer set for {WRITING_TIME_SECONDS}s.")
    return True

def tally_votes():
    """Calculates scores based on votes and determines the winner."""
    vote_counts = {}
    for voter_id, voted_for_id in game_state['votes'].items():
         # Ensure voter is a named player and voted for a caption by a named player
         if voter_id in get_named_players() and voted_for_id in game_state['captions'] and voted_for_id in get_named_players():
            vote_counts[voted_for_id] = vote_counts.get(voted_for_id, 0) + 1

    winning_caption_id = None
    max_votes = -1

    if vote_counts:
        max_votes = max(vote_counts.values())
        # Find all players who got max votes (handle ties simply by picking one)
        potential_winners = [p_id for p_id, count in vote_counts.items() if count == max_votes]
        if potential_winners:
            winning_caption_id = random.choice(potential_winners)

    for author_id, count in vote_counts.items():
        if author_id in game_state['players']:
            game_state['players'][author_id]['score'] += count # Each vote is 1 point

    game_state['winning_caption_id'] = winning_caption_id
    winner_name = game_state['players'][winning_caption_id]['name'] if winning_caption_id and winning_caption_id in game_state['players'] else "None"
    print(f"Votes tallied. Round winner: {winner_name} with {max_votes if winning_caption_id else 0} votes.")

def get_named_players():
    """Returns a list of player_ids who have set a name other than the default."""
    return [p_id for p_id, p_data in game_state['players'].items() if p_data.get('name') and p_data['name'] != 'Unnamed Player']

def check_all_submitted():
    """Checks if all named players have submitted BOTH caption texts."""
    named_player_ids = get_named_players()
    if not named_player_ids: return False
    # Check if the caption entry exists and has both text fields populated AND at least one is non-empty
    players_not_submitted = [p_id for p_id in named_player_ids if not game_state['captions'].get(p_id) or (not game_state['captions'][p_id].get('text1') and not game_state['captions'][p_id].get('text2'))]
    return len(players_not_submitted) == 0


def check_all_voted():
    """Checks if all named players who *submitted a caption* have voted."""
    # Only named players who successfully submitted BOTH caption texts (or at least one) are expected to vote.
    named_players_who_submitted = [p_id for p_id, caption_data in game_state['captions'].items() if p_id in get_named_players() and (caption_data.get('text1') or caption_data.get('text2'))]
    if not named_players_who_submitted: return True

    voters_needed = [p_id for p_id in named_players_who_submitted if game_state['players'].get(p_id) and not game_state['players'][p_id].get('voted_this_round')]
    return len(voters_needed) == 0

def check_and_advance_state_if_timer_expired():
    """Checks if the current phase timer has expired and transitions the state."""
    current_time = time.time()
    if game_state['state'] in ['writing', 'voting'] and game_state.get('phase_end_time') is not None and current_time > game_state['phase_end_time']:
        print(f"Timer expired for state {game_state['state']}. Advancing state...")
        if game_state['state'] == 'writing':
            print("Transitioning from writing to voting due to timer.")
            game_state['state'] = 'voting'
            game_state['phase_end_time'] = current_time + VOTING_TIME_SECONDS # Start voting timer
            print(f"Transitioned to voting. Voting timer set for {VOTING_TIME_SECONDS}s.")
            for player_data in game_state['players'].values():
                player_data['voted_this_round'] = False # Reset voted status for the new voting phase

        elif game_state['state'] == 'voting':
            print("Transitioning from voting to round_results due to timer.")
            game_state['state'] = 'round_results'
            game_state['phase_end_time'] = None
            tally_votes() # Tally votes when voting time is up

# --- Image Rendering Function ---

def render_caption_on_image(poster_path, text1, text2):
    """Renders text1 and text2 onto the poster image dynamically."""
    full_poster_path = os.path.join(app.static_folder, poster_path)
    print(f"\n--- RENDER START ---")
    print(f"RENDER_DEBUG: Attempting to render on poster: {full_poster_path}")
    print(f"RENDER_DEBUG: Caption Text 1: '{text1}', Text 2: '{text2}'")

    try:
        img = Image.open(full_poster_path).convert("RGB") # Ensure RGB mode for inversion
        draw = ImageDraw.Draw(img)
        img_width, img_height = img.size
        print(f"RENDER_DEBUG: Opened image. Size: {img_width}x{img_height}, Mode: {img.mode}")

        # --- Dynamic Font Loading and Sizing ---
        title_font = None
        body_font = None

        # Calculate dynamic font sizes based on image height percentages
        title_font_size = int(img_height * (TITLE_FONT_SIZE_PERCENT_OF_HEIGHT / 100))
        body_font_size = int(img_height * (BODY_FONT_SIZE_PERCENT_OF_HEIGHT / 100))

        # Ensure font size is not zero or negative
        title_font_size = max(1, title_font_size)
        body_font_size = max(1, body_font_size)

        print(f"RENDER_DEBUG: Calculated Font Sizes: Title={title_font_size}px, Body={body_font_size}px")

        try:
            # Construct full paths to fonts within the static folder
            # Use os.path.join and app.static_folder correctly
            full_title_font_path = os.path.join(app.static_folder, TITLE_FONT_PATH.replace('static/', ''))
            full_body_font_path = os.path.join(app.static_folder, BODY_FONT_PATH.replace('static/', ''))


            print(f"RENDER_DEBUG: Attempting to load Title Font from: {full_title_font_path}")
            print(f"RENDER_DEBUG: Attempting to load Body Font from: {full_body_font_path}")

            title_font = ImageFont.truetype(full_title_font_path, title_font_size)
            body_font = ImageFont.truetype(full_body_font_path, body_font_size)
            print(f"RENDER_DEBUG: Custom fonts loaded successfully.")

        except IOError as e:
             print(f"RENDER_DEBUG: Could not load custom font files. Error: {e}. Rendering with default font.")
             try:
                 title_font = ImageFont.load_default()
                 body_font = ImageFont.load_default()
                 print("RENDER_DEBUG: Default fonts loaded.")
                 # Note: Default font rendering/measurement is simplified.
                 # Calculated sizes/positions may not align visually.
                 # Truncation might be less precise with default font.

             except Exception as e:
                 print(f"RENDER_DEBUG: Could load default font either: {e}")
                 return None

        if not title_font or not body_font:
             print("RENDER_DEBUG: No usable fonts loaded. Rendering failed.")
             return None


        # --- Dynamic Positioning and Text Layout ---

        # Calculate dynamic text box widths based on image width percentages
        title_box_width = int(img_width * (TITLE_WIDTH_PERCENT / 100))
        body_box_width = int(img_width * (BODY_WIDTH_PERCENT / 100))

        # Ensure text box widths are positive and not exceeding image width
        title_box_width = max(1, min(img_width, title_box_width))
        body_box_width = max(1, min(img_width, body_box_width))

        print(f"RENDER_DEBUG: Calculated Box Widths: Title={title_box_width}px, Body={body_box_width}px")


        # Calculate dynamic top positions based on image height percentages
        title_top_y = int(img_height * (TITLE_TOP_PERCENT / 100))
        body_top_y = int(img_height * (BODY_TOP_PERCENT / 100))

        # Ensure top positions are not negative
        title_top_y = max(0, title_top_y)
        body_top_y = max(0, body_top_y)

        print(f"RENDER_DEBUG: Calculated Top Positions: Title={title_top_y}px, Body={body_top_y}px")


        # Calculate horizontal *center* position for drawing the text lines
        # This is the center of the image width
        center_x = img_width // 2


        # Helper to get line width reliably - Pass estimated_font_size for fallback
        def get_text_width(txt, fnt, estimated_font_size):
             if not txt: return 0 # Empty string has 0 width
             try:
                 if hasattr(fnt, 'textbbox'):
                      bbox = fnt.textbbox((0,0), txt)
                      return bbox[2]
                 elif hasattr(fnt, 'getsize'): # Deprecated, but fallback
                      return fnt.getsize(txt)[0]
             except Exception as e:
                  # print(f"RENDER_DEBUG: Error measuring text width for '{txt[:20]}...' with font {fnt}: {e}") # Keep commented
                  pass # Fail gracefully
             # Fallback estimation if measurement fails
             # Estimate width based on font size and average character width multiplier (e.g., 0.6)
             # This helps layout_text_lines make better decisions even if pixel measurement fails
             # Ensure estimated_font_size is not zero before multiplying
             if estimated_font_size <= 0:
                 return int(len(txt) * 10) # Default small estimate if font size is tiny

             return int(len(txt) * estimated_font_size * 0.6) # <-- Added estimation here


        # Helper to get line height reliably
        def get_text_height(txt, fnt, estimated_font_size, line_height_multiplier):
             if not txt: # Height of an empty line for spacing purposes
                 try:
                     if hasattr(fnt, 'getmetrics'):
                         ascent, descent = fnt.getmetrics()
                         return int((ascent + descent) * line_height_multiplier)
                     elif hasattr(fnt, 'textbbox'):
                         # Try measuring a placeholder character if textbbox exists but text is empty
                         try: return int(fnt.textbbox((0,0), 'X')[3] * line_height_multiplier)
                         except: pass
                 except Exception:
                     pass
                 # Ensure estimated_font_size is not zero before multiplying
                 if estimated_font_size <= 0:
                      return int(10 * line_height_multiplier) # Default small estimate

                 return int(estimated_font_size * line_height_multiplier)

             try:
                 if hasattr(fnt, 'textbbox'):
                      return int(estimated_font_size * line_height_multiplier)
                 elif hasattr(fnt, 'getsize'): # Deprecated, but fallback
                      return int(fnt.getsize(txt)[1] * line_height_multiplier)
             except Exception as e:
                  print(f"RENDER_DEBUG: Error measuring text height for '{txt[:20]}...' with font {fnt}: {e}")
             # Fallback estimation
             # Ensure estimated_font_size is not zero before multiplying
             if estimated_font_size <= 0:
                  return int(10 * line_height_multiplier) # Default small estimate

             return int(estimated_font_size * line_height_multiplier)


        # Helper for word wrapping + fallback truncation if a single word is too wide
        # This version does word wrapping first, and if a single word is still too wide, it puts that word on a line
        # and that single line might visually overflow the box if it's still too long after estimation.
        def layout_text_lines(text, font, max_width_pixels, estimated_font_size): # Pass estimated_font_size
            print(f"RENDER_DEBUG: layout_text_lines Input: '{text}', MaxWidth: {max_width_pixels}, EstFontSize: {estimated_font_size}")
            if not text: return []

            wrapped_lines = []
            words = text.split()
            current_line_words = []
            # Safe max width uses the actual max_width_pixels calculated from percentage
            # Add a small buffer to the safe width
            safe_max_width = max_width_pixels * 0.98 # Use 98% of box width for safety margin


            for word in words:
                test_line = ' '.join(current_line_words + [word])
                line_width_pixels = get_text_width(test_line, font, estimated_font_size)
                print(f"RENDER_DEBUG:   Testing line '{test_line[:20]}...', Width: {line_width_pixels}, Safe Max: {safe_max_width}")


                if line_width_pixels == -1 or line_width_pixels > safe_max_width:
                    # If measurement failed OR adding the word makes the line too wide
                    print(f"RENDER_DEBUG:   Line overflow or measurement failed. Current: '{' '.join(current_line_words)}', Next word: '{word}'")
                    if current_line_words: # If there's content in the current line
                         wrapped_lines.append(' '.join(current_line_words)) # Finalize the current line
                         print(f"RENDER_DEBUG:   Wrapped line: '{wrapped_lines[-1]}'")
                         current_line_words = [word] # Start a new line with the current word
                         print(f"RENDER_DEBUG:   Starting new line with: '{word}'")
                    else: # If the *single word itself* is too wide or measurement failed, add it as its own line
                         # In this version, we allow single words to overflow visually if needed after measurement fallback
                         # We should still ensure it's not an empty string if the word wasn't empty
                         if word: # Only add the word if it's not empty
                             wrapped_lines.append(word)
                             print(f"RENDER_DEBUG:   Added single word line (might overflow): '{word}'")
                         current_line_words = [] # Start a new empty line
                         print(f"RENDER_DEBUG:   Starting new empty line.")
                else:
                    current_line_words.append(word) # Add word to current line
                    # print(f"RENDER_DEBUG:   Added word '{word}' to current line. Current line: '{' '.join(current_line_words)}'") # Keep commented, too verbose


            if current_line_words: # Add any remaining words as the last wrapped line
                wrapped_lines.append(' '.join(current_line_words))
                print(f"RENDER_DEBUG: Added remaining current line: '{wrapped_lines[-1]}'")

            # Remove any purely empty strings that might have been generated by logic errors or multiple splits
            final_lines = [line for line in wrapped_lines if line]

            # If the original input text was NOT empty, but the final lines list IS empty,
            # it indicates a layout/truncation failure resulted in everything being removed.
            # In this specific case, add at least an empty line to maintain vertical flow.
            if text and not final_lines:
                 final_lines = [""]
                 print("RENDER_DEBUG: Input text was not empty, but layout resulted in empty lines. Adding single empty line.")


            print(f"RENDER_DEBUG: layout_text_lines Output (after cleaning empty): {final_lines}")
            return final_lines


        # Split input text by explicit newlines (\n) first, then layout each resulting part
        text1_safe = text1 if text1 is not None else ''
        text2_safe = text2 if text2 is not None else ''

        # Process Text 1 (Title) - Apply layout to each line split by \n
        processed_text1_lines = []
        for part in text1_safe.upper().split('\n'):
            processed_text1_lines.extend(layout_text_lines(part, title_font, title_box_width, title_font_size))

        # Process Text 2 (Body) - Apply layout to each line split by \n
        processed_text2_lines = []
        for part in text2_safe.upper().split('\n'):
             processed_text2_lines.extend(layout_text_lines(part, body_font, body_box_width, body_font_size))


        print(f"RENDER_DEBUG: Final Processed Text 1 lines (for drawing): {processed_text1_lines}")
        print(f"RENDER_DEBUG: Final Processed Text 2 lines (for drawing): {processed_text2_lines}")


        # --- Draw Text with Inverted Color using Anchor (Color sampled ONCE per block) ---

        # Text 1 (Title)
        title_inverted_color = (255, 255, 255) # Default to white

        # Sample color under the first *non-empty* processed line if it exists
        first_title_line = next((line for line in processed_text1_lines if line), None)

        if first_title_line:
            first_line_height = get_text_height(first_title_line, title_font, title_font_size, 1.2)
            # Calculate sample point (horizontal center of the image, vertical center of the first line)
            sample_x = max(0, min(img_width - 1, center_x)) # Sample at horizontal image center
            sample_y = max(0, min(img_height - 1, title_top_y + first_line_height // 2))

            try:
                bg_color = img.getpixel((sample_x, sample_y))
                title_inverted_color = (255 - bg_color[0], 255 - bg_color[1], 255 - bg_color[2])
            except Exception as e:
                 print(f"RENDER_DEBUG: Error sampling pixel for T1 at ({sample_x}, {sample_y}): {e}. Defaulting color.")
                 title_inverted_color = (255, 255, 255)


        y_offset = title_top_y
        for line in processed_text1_lines:
            # Use actual title_font_size for get_text_height, even for empty lines
            line_height = get_text_height(line, title_font, title_font_size, 1.2)

            if not line:
                 # Even if line is empty, advance y_offset by height of an empty line for spacing
                 y_offset += line_height
                 continue

            line_center_y = y_offset + line_height // 2

            try:
                # print(f"RENDER_DEBUG: Drawing T1 Line '{line}' at CENTER_X={center_x}, CENTER_Y={line_center_y} with color {title_inverted_color}")
                draw.text((center_x, line_center_y), line, fill=title_inverted_color, font=title_font, anchor='mm')
            except Exception as e:
                 print(f"RENDER_DEBUG: Error drawing Text 1 line '{line}': {e}")

            y_offset += line_height


        # Text 2 (Body)
        body_inverted_color = (255, 255, 255) # Default to white

        # Sample color under the first *non-empty* truncated line if it exists
        first_body_line = next((line for line in processed_text2_lines if line), None)

        if first_body_line:
            first_line_height = get_text_height(first_body_line, body_font, body_font_size, BODY_LINE_HEIGHT_MULTIPLIER)
            sample_x = max(0, min(img_width - 1, center_x))
            sample_y = max(0, min(img_height - 1, body_top_y + first_line_height // 2))

            try:
                bg_color = img.getpixel((sample_x, sample_y))
                body_inverted_color = (255 - bg_color[0], 255 - bg_color[1], 255 - bg_color[2])
            except Exception as e:
                 print(f"RENDER_DEBUG: Error sampling pixel for T2 at ({sample_x}, {sample_y}): {e}. Defaulting color.")
                 body_inverted_color = (255, 255, 255)


        y_offset = body_top_y
        for line in processed_text2_lines:
             # Use actual body_font_size for get_text_height, even for empty lines
            body_line_height = get_text_height(line, body_font, body_font_size, BODY_LINE_HEIGHT_MULTIPLIER)

            if not line:
                 y_offset += body_line_height
                 continue

            line_center_y = y_offset + body_line_height // 2

            try:
                 draw.text((center_x, line_center_y), line, fill=body_inverted_color, font=body_font, anchor='mm')
            except Exception as e:
                 print(f"RENDER_DEBUG: Error drawing Text 2 line '{line}': {e}")

            y_offset += body_line_height

        print("--- RENDER END ---")
        return img

    except FileNotFoundError:
        print(f"RENDER_DEBUG: ERROR: Poster image not found at {full_poster_path}")
        print("--- RENDER END ---")
        return None
    except Exception as e:
        print(f"RENDER_DEBUG: ERROR during image rendering for {poster_path}: {e}")
        print("--- RENDER END ---")
        # Re-raise the exception here to get a full traceback if needed for deeper debugging
        # raise e
        return None


# --- Routes ---

@app.route('/')
def index():
    return redirect(url_for('lobby'))

@app.route('/game_state_check')
def game_state_check():
    check_and_advance_state_if_timer_expired()
    return jsonify({'state': game_state['state'], 'phase_end_time': game_state.get('phase_end_time')})

@app.route('/rendered_caption/<caption_author_id>')
def rendered_caption(caption_author_id):
    if caption_author_id not in game_state['captions']:
        print(f"RENDER_DEBUG: Caption author ID {caption_author_id} not found in captions.")
        return "Caption not found", 404
    caption_data = game_state['captions'][caption_author_id]
    text1 = caption_data.get('text1', '')
    text2 = caption_data.get('text2', '')

    if not game_state.get('current_poster'):
        print(f"RENDER_DEBUG: No current poster set for round {game_state.get('current_round')}.")
        return "No poster set for this round", 404

    rendered_img = render_caption_on_image(game_state['current_poster'], text1, text2)

    if rendered_img is None:
        print(f"RENDER_DEBUG: render_caption_on_image returned None for author {caption_author_id}. Check rendering errors printed above.")
        return "Could not render image", 500
    else:
        try:
            img_byte_arr = io.BytesIO()
            rendered_img.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            print(f"RENDER_DEBUG: Successfully rendered and sending image for author {caption_author_id}.")
            return send_file(img_byte_arr, mimetype='image/png', as_attachment=False)
        except Exception as e:
             print(f"RENDER_DEBUG: ERROR saving or sending rendered image for author {caption_author_id}: {e}")
             return "Error saving/sending image", 500


@app.route('/lobby', methods=['GET', 'POST'])
def lobby():
    player_id = get_player_id()
    if game_state['state'] != 'lobby':
        current_player = get_current_player()
        if current_player:
             print(f"Game in progress ({game_state['state']}), redirecting player {current_player.get('name')} ({player_id}) from lobby.")
             return redirect(url_for(game_state['state']))
        else:
             print(f"Game in progress ({game_state['state']}), new/unknown session {player_id} trying to join lobby.")
             flash("A game is currently in progress. Please wait for it to finish.")
             return render_template('lobby.html', game_state=game_state, current_player=None, game_in_progress=True)

    if player_id not in game_state['players']:
         game_state['players'][player_id] = {
             'name': 'Unnamed Player', 'score': 0, 'submitted_this_round': False, 'voted_this_round': False
         }

    current_player = game_state['players'][player_id]

    if request.method == 'POST':
        player_name = request.form.get('player_name', '').strip()
        if player_name:
            current_player['name'] = player_name
            flash(f"Your name is now {player_name}!")
        else:
             flash("Name cannot be empty. Using default name.")

        return redirect(url_for('lobby'))

    return render_template('lobby.html', game_state=game_state, current_player=current_player, game_in_progress=False, current_session_id=session.get('player_id'))


@app.route('/start_game', methods=['POST'])
def start_game():
    player_id = get_player_id()
    current_player = get_current_player()
    print(f"Attempting to start game by player {player_id}. State: {game_state['state']}")

    if game_state['state'] == 'lobby' and current_player:
        named_players_count = len(get_named_players())
        print(f"Start game check: Named player count: {named_players_count}")

        if named_players_count < 2:
            flash("Need at least 2 players with names to start!")
            print("Start game failed: Not enough named players.")
            return redirect(url_for('lobby'))

        if current_player.get('name') == 'Unnamed Player':
             flash("Please set your name before starting the game.")
             print(f"Start game failed: Player {player_id} is unnamed.")
             return redirect(url_for('lobby'))

        if not game_state['all_posters']:
             flash("Error: No posters found in static/posters directory! Cannot start game.")
             print("ERROR: game_state['all_posters'] is empty in start_game despite before_request.")
             game_state['state'] = 'lobby'
             return redirect(url_for('lobby'))

        print("Starting game...")
        if start_new_round():
            return redirect(url_for('writing'))
        else:
            flash("Could not start a new round. Check server logs.")
            print("Failed to start new round.")
            return redirect(url_for('lobby'))

    if not current_player:
        flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
    else:
        print(f"Start game request denied: player {current_player.get('name')} on wrong page, state {game_state['state']}.")
        return redirect(url_for(game_state['state']))


@app.route('/writing')
def writing():
    player_id = get_player_id()
    current_player = get_current_player()
    if game_state['state'] != 'writing' or not current_player or current_player.get('submitted_this_round'):
         if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
         elif game_state['state'] == 'writing' and current_player.get('submitted_this_round'): return redirect(url_for('wait'))
         else: return redirect(url_for(game_state['state']))

    if current_player.get('name') == 'Unnamed Player':
         flash("Please set your name in the lobby to participate in the round."); return redirect(url_for('lobby'))

    return render_template('writing.html', game_state=game_state, current_player=current_player, phase_end_time=game_state.get('phase_end_time', 0))

@app.route('/submit_caption', methods=['POST'])
def submit_caption():
    player_id = get_player_id()
    current_player = get_current_player()
    current_time = time.time()
    timer_expired = game_state.get('phase_end_time') is not None and current_time > game_state['phase_end_time']

    if game_state['state'] == 'writing' and current_player and not current_player.get('submitted_this_round') and current_player.get('name') != 'Unnamed Player' and not timer_expired:
        caption_text1 = request.form.get('caption_text1', '').strip()
        caption_text2 = request.form.get('caption_text2', '').strip()

        if caption_text1 or caption_text2:
            game_state['captions'][player_id] = {'text1': caption_text1, 'text2': caption_text2}
            current_player['submitted_this_round'] = True
            print(f"Player {current_player['name']} ({player_id}) submitted caption.")

            if check_all_submitted():
                print("All named players submitted early. Moving to voting.")
                game_state['state'] = 'voting'
                game_state['phase_end_time'] = current_time + VOTING_TIME_SECONDS
                for player_data in game_state['players'].values():
                     player_data['voted_this_round'] = False
                return redirect(url_for('voting'))
            else:
                 print("Waiting for more submissions or timer.")
                 return redirect(url_for('wait'))

        else:
            flash("Please enter at least one line for your caption."); return redirect(url_for('writing'))

    elif timer_expired:
         flash("Time is up! Your caption was not submitted."); return redirect(url_for('wait'))
    else:
         if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
         return redirect(url_for(game_state['state']))


@app.route('/voting')
def voting():
    player_id = get_player_id()
    current_player = get_current_player()
    if game_state['state'] != 'voting' or not current_player or current_player.get('voted_this_round'):
         if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
         elif game_state['state'] == 'voting' and current_player.get('voted_this_round'): return redirect(url_for('wait'))
         else: return redirect(url_for(game_state['state']))

    if current_player.get('name') == 'Unnamed Player':
         flash("Please set your name in the lobby to participate in the round."); return redirect(url_for('lobby'))

    voteable_caption_authors = [p_id for p_id, caption_data in game_state['captions'].items() if p_id in get_named_players() and p_id != player_id and (caption_data.get('text1') or caption_data.get('text2'))]

    shuffled_voteable_authors = voteable_caption_authors.copy()
    random.shuffle(shuffled_voteable_authors)

    if not shuffled_voteable_authors and player_id in game_state['captions'] and player_id in get_named_players():
        print(f"Player {current_player['name']} ({player_id}) submitted but had no one else to vote for. Auto-marking as voted.")
        current_player['voted_this_round'] = True
        if check_all_voted():
            print("Voting complete (auto-skipped for one). Tallying results.")
            tally_votes()
            game_state['state'] = 'round_results'
            game_state['phase_end_time'] = None
            return redirect(url_for('round_results'))

    return render_template('voting.html', game_state=game_state, current_player=current_player, voteable_author_ids=shuffled_voteable_authors, phase_end_time=game_state.get('phase_end_time'))

@app.route('/submit_vote', methods=['POST'])
def submit_vote():
    player_id = get_player_id()
    current_player = get_current_player()
    current_time = time.time()
    timer_expired = game_state.get('phase_end_time') is not None and current_time > game_state['phase_end_time']

    if game_state['state'] == 'voting' and current_player and not current_player.get('voted_this_round') and current_player.get('name') != 'Unnamed Player' and not timer_expired:
        voted_for_id = request.form.get('vote')

        if voted_for_id and voted_for_id in game_state['captions'] and voted_for_id in get_named_players() and voted_for_id != player_id and voted_for_id in game_state['players']:
            game_state['votes'][player_id] = voted_for_id
            current_player['voted_this_round'] = True
            print(f"Player {current_player['name']} ({player_id}) voted.")

            if check_all_voted():
                print("All relevant players voted early. Tallying results.")
                tally_votes()
                game_state['state'] = 'round_results'
                game_state['phase_end_time'] = None
                return redirect(url_for('round_results'))

        else:
            flash("Invalid vote submitted."); return redirect(url_for('voting'))

    elif timer_expired:
        flash("Time is up! Your vote was not submitted."); return redirect(url_for('wait'))
    else:
        if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
        return redirect(url_for(game_state['state']))

    if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
    return redirect(url_for(game_state['state']))


@app.route('/round_results')
def round_results():
    player_id = get_player_id()
    current_player = get_current_player()
    if game_state['state'] != 'round_results' or not current_player:
        if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
        else: return redirect(url_for(game_state['state']))

    results = []
    vote_counts = {}
    for voter_id, voted_for_id in game_state['votes'].items():
        if voter_id in get_named_players() and voted_for_id in game_state['captions'] and voted_for_id in get_named_players():
             vote_counts[voted_for_id] = vote_counts.get(voted_for_id, 0) + 1

    named_caption_authors = [p_id for p_id, caption_data in game_state['captions'].items() if p_id in get_named_players() and (caption_data.get('text1') or caption_data.get('text2'))]

    for author_id in named_caption_authors:
         caption_data = game_state['captions'][author_id]
         author_name = game_state['players'][author_id]['name']
         results.append({
             'author_id': author_id,
             'author_name': author_name,
             'caption_text1': caption_data.get('text1', ''),
             'caption_text2': caption_data.get('text2', ''),
             'votes': vote_counts.get(author_id, 0),
             'is_winner': (author_id == game_state['winning_caption_id'])
         })

    results.sort(key=lambda x: x['votes'], reverse=True)
    sorted_players = sorted([p for p in game_state['players'].values() if p.get('name') != 'Unnamed Player'], key=lambda x: x['score'], reverse=True)

    is_game_over = game_state['current_round'] >= 5

    # --- NEW: If game is over, set the state to 'game_over' now ---
    if is_game_over:
        print(f"Round {game_state['current_round']} is the final round. Setting state to 'game_over'.")
        game_state['state'] = 'game_over'
        game_state['phase_end_time'] = None # Clear timer

    return render_template('round_results.html',
                           game_state=game_state, current_player=current_player,
                           results=results, sorted_players=sorted_players,
                           is_game_over=is_game_over) # is_game_over still used by template to show correct button/message

@app.route('/next_round', methods=['POST'])
def next_round():
    player_id = get_player_id()
    current_player = get_current_player()
    print(f"Next round request: player_id={player_id}, state={game_state['state']}")

    if game_state['state'] == 'round_results' and current_player:
        if game_state['current_round'] < 5:
            if current_player.get('name') == 'Unnamed Player':
               flash("Please set your name to proceed."); return redirect(url_for('round_results'))

            players_to_remove = [p_id for p_id, p_data in game_state['players'].items() if p_data.get('name') == 'Unnamed Player']
            for p_id in players_to_remove:
                 print(f"Removing inactive player: {p_id}")
                 if p_id != player_id:
                    del game_state['players'][p_id]

            named_players_count = len(get_named_players())
            print(f"Checking player count for next round: {named_players_count}")
            if named_players_count < 2:
                 flash("Not enough players to continue. Returning to lobby."); print("Less than 2 named players, resetting game.")
                 game_state.update({
                     'players': {}, 'state': 'lobby', 'current_round': 0, 'current_poster': None,
                     'captions': {}, 'votes': {}, 'posters_used': [], 'winning_caption_id': None,
                     'phase_end_time': None
                 })
                 return redirect(url_for('lobby'))

            if start_new_round():
                print("Starting next round."); return redirect(url_for('writing'))
            else:
                 flash("Could not start next round. Check server logs."); print("Failed to start next round.")
                 return redirect(url_for('round_results'))
        else:
            print("Game is over, redirecting to game over page."); game_state['state'] = 'game_over'; game_state['phase_end_time'] = None
            return redirect(url_for('game_over'))

    if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
    else: return redirect(url_for(game_state['state']))


@app.route('/game_over')
def game_over():
    player_id = get_player_id()
    current_player = get_current_player()
    if game_state['state'] != 'game_over' or not current_player:
        if not current_player: flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))
        else: return redirect(url_for(game_state['state']))

    final_scores = sorted([p for p in game_state['players'].values() if p.get('name') != 'Unnamed Player'], key=lambda x: x['score'], reverse=True)
    return render_template('game_over.html', final_scores=final_scores, current_player=current_player)

@app.route('/reset_game', methods=['POST'])
def reset_game():
    player_id = get_player_id()
    print(f"Resetting game state requested by {player_id}")
    game_state.update({
        'players': {}, 'state': 'lobby', 'current_round': 0, 'current_poster': None,
        'captions': {}, 'votes': {}, 'posters_used': [], 'winning_caption_id': None, 'phase_end_time': None
    })
    flash("Game state has been reset. Starting a new game!"); return redirect(url_for('lobby'))


@app.route('/wait')
def wait():
     player_id = get_player_id()
     current_player = get_current_player()
     check_and_advance_state_if_timer_expired()

     if game_state['state'] != 'writing' and game_state['state'] != 'voting':
         print(f"Wait page: State is now {game_state['state']}, redirecting player {current_player.get('name', 'Unknown') if current_player else 'Unknown Player'}.")
         return redirect(url_for(game_state['state']))

     if not current_player:
         flash("Please join the game in the lobby first."); return redirect(url_for('lobby'))

     message = "Please wait..."
     if game_state['state'] == 'writing':
         if not current_player.get('submitted_this_round'):
             print(f"Wait page: Player {current_player.get('name')} hasn't submitted, redirecting to writing."); return redirect(url_for('writing'))
         message = "Waiting for other players to submit their captions..."
     elif game_state['state'] == 'voting':
          if not current_player.get('voted_this_round'):
              print(f"Wait page: Player {current_player.get('name')} hasn't voted, redirecting to voting."); return redirect(url_for('voting'))
          message = "Waiting for other players to vote..."

     players_for_wait_list = []
     for p_id, p_data in game_state['players'].items():
          player_entry = p_data.copy()
          player_entry['player_id'] = p_id
          players_for_wait_list.append(player_entry)

     sorted_wait_players = sorted(players_for_wait_list, key=lambda x: x.get('name', 'Unnamed Player'))

     return render_template('wait.html', message=message, game_state=game_state, current_player=current_player, sorted_players=sorted_wait_players, session_id=player_id, refresh_seconds=WAIT_PAGE_REFRESH_SECONDS)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.before_request
def initialize_player_session_and_posters():
    get_player_id()
    if not game_state['all_posters']:
        print("DEBUG: game_state['all_posters'] is empty. Attempting to load posters in before_request...")
        loaded_posters = load_all_posters()
        if loaded_posters: game_state['all_posters'] = loaded_posters; print(f"DEBUG: Successfully loaded {len(game_state['all_posters'])} posters in before_request.")
        else: print("DEBUG: Still no posters loaded after attempt in before_request.")


if __name__ == '__main__':
    os.makedirs(os.path.join(app.static_folder, 'posters'), exist_ok=True)
    os.makedirs(os.path.join(app.static_folder, 'fonts'), exist_ok=True)
    app.run(debug=True)