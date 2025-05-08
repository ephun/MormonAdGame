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
WRITING_TIME_SECONDS = 60
VOTING_TIME_SECONDS = 20
WAIT_PAGE_REFRESH_SECONDS = 1 # Meta refresh speed on wait page

# --- Font and Rendering Configuration (Adjust paths and ratios as needed) ---
TITLE_FONT_PATH = 'static/fonts/title.otf' # Adjust path if you put fonts elsewhere
BODY_FONT_PATH = 'static/fonts/body.ttf'   # Adjust path if you put fonts elsewhere
# Ratios are based on your 500px height example image (assuming width 300 proportional to height)
BASE_HEIGHT_EXAMPLE = 500
BASE_WIDTH_EXAMPLE = 300

# Text 1 (Title) - scaled based on image height
TITLE_TOP_RATIO = 50 / BASE_HEIGHT_EXAMPLE # 0.1
TITLE_WIDTH_RATIO = 300 / BASE_HEIGHT_EXAMPLE # 0.6
TITLE_FONT_SIZE_RATIO = 40 / BASE_HEIGHT_EXAMPLE # 0.08
TITLE_MAX_CHARS_PER_LINE = 15 # Estimated chars before wrapping for title font/size/width

# Text 2 (Body) - scaled based on image height
BODY_TOP_RATIO = 400 / BASE_HEIGHT_EXAMPLE # 0.8
BODY_WIDTH_RATIO = 300 / BASE_HEIGHT_EXAMPLE # 0.6
BODY_FONT_SIZE_RATIO = 15 / BASE_HEIGHT_EXAMPLE # 0.02
BODY_MAX_CHARS_PER_LINE = 35 # Estimated chars before wrapping for body font/size/width
BODY_LINE_HEIGHT_RATIO = 1.2 # Adjust line height multiplier if needed

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
        print(f"Generated new session ID: {session['player_id']}") # Debug print
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
    # print(f"DEBUG: Looking for posters in directory: {poster_dir}") # Keep commented for less noise
    if not os.path.exists(poster_dir):
        print(f"DEBUG: Warning: Posters directory not found at {poster_dir}")
        return []
    try:
        poster_files = [f for f in os.listdir(poster_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))]
        # print(f"DEBUG: Found {len(poster_files)} potential poster files.") # Keep commented for less noise
        poster_paths = [f'posters/{f}' for f in poster_files]
        # print(f"DEBUG: Loaded {len(poster_paths)} poster paths.") # Keep commented for less noise
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
        # Ensure potential_winners is not empty before random.choice
        if potential_winners:
            winning_caption_id = random.choice(potential_winners)


    # Update scores for all players who received votes
    for author_id, count in vote_counts.items():
        if author_id in game_state['players']: # Ensure the player still exists
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
    # Ensure app context is available for url_for and static_folder if this is called outside a request
    # It's called from within request contexts (/rendered_caption), so static_folder should be available.
    full_poster_path = os.path.join(app.static_folder, poster_path)
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
        # Calculate dynamic font sizes
        title_font_size = int(img_height * TITLE_FONT_SIZE_RATIO)
        body_font_size = int(img_height * BODY_FONT_SIZE_RATIO)
        # Ensure font size is not zero or negative
        title_font_size = max(1, title_font_size)
        body_font_size = max(1, body_font_size)

        try:
            # Construct full paths to fonts within the static folder
            full_title_font_path = os.path.join(app.static_folder, TITLE_FONT_PATH.replace('static/', ''))
            full_body_font_path = os.path.join(app.static_folder, BODY_FONT_PATH.replace('static/', ''))

            print(f"RENDER_DEBUG: Attempting to load Title Font from: {full_title_font_path}")
            print(f"RENDER_DEBUG: Attempting to load Body Font from: {full_body_font_path}")

            title_font = ImageFont.truetype(full_title_font_path, title_font_size)
            body_font = ImageFont.truetype(full_body_font_path, body_font_size)
            print(f"RENDER_DEBUG: Custom fonts loaded. Title size: {title_font_size}, Body size: {body_font_size}")

        except IOError as e:
             print(f"RENDER_DEBUG: Could not load custom font files. Error: {e}. Rendering with default font.")
             try:
                 # Fallback to a default PIL font if custom fonts fail
                 # Default font doesn't support truetype, load directly
                 title_font = ImageFont.load_default()
                 body_font = ImageFont.load_default()
                 print("RENDER_DEBUG: Default fonts loaded.")
                 # Note: Default font sizing/positioning will be different.
                 # We won't have reliable font.size or textbbox/getsize pixel measurements.
                 # We'll have to rely more on character count estimates for wrapping fallback.
                 # A default font size estimate might be needed if using getsize/textbbox isn't possible.

             except Exception as e:
                 print(f"RENDER_DEBUG: Could not load default font either: {e}")
                 return None # Cannot render without any font

        # If font loading failed entirely
        if not title_font or not body_font:
             print("RENDER_DEBUG: No usable fonts loaded. Rendering failed.")
             return None


        # --- Dynamic Positioning and Text Wrapping ---
        # Calculate dynamic text box widths (relative to image height as per example)
        title_box_width = int(img_height * TITLE_WIDTH_RATIO)
        body_box_width = int(img_height * BODY_WIDTH_RATIO)

        # Ensure text box widths are positive and not exceeding image width
        title_box_width = max(1, min(img_width, title_box_width))
        body_box_width = max(1, min(img_width, body_box_width))

        # Calculate dynamic top positions (relative to image height)
        title_top_y = int(img_height * TITLE_TOP_RATIO)
        body_top_y = int(img_height * BODY_TOP_RATIO)

        # Ensure top positions are within image bounds (roughly) - can't be negative, can be >= height-1
        title_top_y = max(0, title_top_y)
        body_top_y = max(0, body_top_y)

        # Calculate horizontal center position for text boxes (defined *before* loops)
        title_left_x = (img_width - title_box_width) // 2
        body_left_x = (img_width - body_box_width) // 2


        # Helper to get line width reliably if possible - DEFINED INSIDE render_caption_on_image
        def get_text_width(txt, fnt):
             try:
                 # Try modern Pillow textbbox first
                 if hasattr(fnt, 'textbbox'):
                      # textbbox returns (left, top, right, bottom)
                      # width is right - left. Assume left is 0 for line measurement.
                      bbox = fnt.textbbox((0,0), txt)
                      return bbox[2] # Return width
                 # Fallback to older getsize (deprecated)
                 elif hasattr(fnt, 'getsize'):
                      return fnt.getsize(txt)[0] # Return width
             except Exception as e:
                  print(f"RENDER_DEBUG: Error measuring text width for '{txt}' with font {fnt} (type: {type(fnt)}): {e}")
             return -1 # Indicate failure

        # Helper for wrapping text - DEFINED INSIDE render_caption_on_image
        def wrap_text(text, font, max_width_pixels): # max_chars_per_line_est removed
            if not text: return []
            lines = []
            words = text.split()
            current_line = []

            for word in words:
                test_line = ' '.join(current_line + [word])
                line_width_pixels = get_text_width(test_line, font)

                # Use a slightly reduced max_width_pixels to be safe against rounding or edge issues
                safe_max_width = max_width_pixels * 0.95 # Example: Use 95% of box width

                if line_width_pixels == -1 or line_width_pixels > safe_max_width:
                    # If measurement failed OR it exceeds safe max width
                    if current_line:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                    else:
                         # If the single word itself cannot be measured or exceeds width,
                         # put it on its own line. It might overflow visually.
                         lines.append(word)
                         current_line = []
                else:
                    current_line.append(word)

            if current_line:
                lines.append(' '.join(current_line))
            return lines


        print(f"RENDER_DEBUG: Wrapping Text 1: '{text1}', Max Width Pixels: {title_box_width}")
        print(f"RENDER_DEBUG: Wrapping Text 2: '{text2}', Max Width Pixels: {body_box_width}")

        # Wrap the caption texts
        # Ensure text is uppercase as per original CSS intention
        wrapped_text1_lines = wrap_text(text1.upper(), title_font, title_box_width)
        wrapped_text2_lines = wrap_text(text2.upper(), body_font, body_box_width)

        print(f"RENDER_DEBUG: Wrapped Text 1 lines: {wrapped_text1_lines}")
        print(f"RENDER_DEBUG: Wrapped Text 2 lines: {wrapped_text2_lines}")


        # --- Draw Text with Inverted Color ---

        # Helper to get line height reliably - DEFINED INSIDE render_caption_on_image
        def get_text_height(txt, fnt, estimated_font_size, line_height_ratio):
             try:
                 if hasattr(fnt, 'textbbox'):
                      # textbbox returns (left, top, right, bottom)
                      # Height is bottom - top.
                      bbox = fnt.textbbox((0,0), txt)
                      # Use estimated font size * line_height_ratio for consistent line spacing
                      # Need to consider asc/desc for accurate line height, but multiplier is a decent estimate
                      return int(estimated_font_size * line_height_ratio)
                 elif hasattr(fnt, 'getsize'):
                      # getsize returns (width, height)
                      return int(fnt.getsize(txt)[1] * line_height_ratio)
             except Exception as e:
                  print(f"RENDER_DEBUG: Error measuring text height for '{txt}' with font {fnt} (type: {type(fnt)}): {e}")
             # Fallback estimation
             return int(estimated_font_size * line_height_ratio)


        # Text 1 (Title)
        y_offset = title_top_y
        for line in wrapped_text1_lines:
            if not line: continue # Skip empty lines

            # Calculate line height
            line_height = get_text_height(line, title_font, title_font_size, 1.2) # Using 1.2 multiplier for title as well for consistency

            # Calculate horizontal center position for THIS line (within the overall box)
            line_width = get_text_width(line, title_font) # Get width using helper
            if line_width == -1: line_width = title_box_width # Fallback if measurement failed

            line_left_x = title_left_x + (title_box_width - line_width) // 2 # Center align within box


            # Get the background color under the approximate center of the line
            # Check bounds before sampling
            sample_x = max(0, min(img_width - 1, line_left_x + line_width // 2))
            sample_y = max(0, min(img_height - 1, y_offset + line_height // 2))

            try:
                bg_color = img.getpixel((sample_x, sample_y))
                inverted_color = (255 - bg_color[0], 255 - bg_color[1], 255 - bg_color[2])
            except Exception as e:
                 inverted_color = (255, 255, 255) # Default to white for any sampling errors
                 print(f"RENDER_DEBUG: Error sampling pixel for T1 at ({sample_x}, {sample_y}): {e}. Defaulting color.")

            try:
                draw.text((line_left_x, y_offset), line, fill=inverted_color, font=title_font)
            except Exception as e:
                 print(f"RENDER_DEBUG: Error drawing Text 1 line '{line}': {e}")

            y_offset += line_height # Move down for the next line


        # Text 2 (Body)
        y_offset = body_top_y # Reset y_offset for body text
        for line in wrapped_text2_lines:
            if not line: continue

             # Calculate line height (Estimate from font size and multiplier)
            body_line_height = get_text_height(line, body_font, body_font_size, BODY_LINE_HEIGHT_RATIO)

             # Calculate horizontal center position for THIS line
            line_width = get_text_width(line, body_font) # Get width using helper
            if line_width == -1: line_width = body_box_width # Fallback if measurement failed

            line_left_x = body_left_x + (body_box_width - line_width) // 2 # Center align within box

             # Get the background color under the approximate center of the line
             # Check bounds before sampling
            sample_x = max(0, min(img_width - 1, line_left_x + line_width // 2))
            sample_y = max(0, min(img_height - 1, y_offset + body_line_height // 2))

            try:
                bg_color = img.getpixel((sample_x, sample_y))
                inverted_color = (255 - bg_color[0], 255 - bg_color[1], 255 - bg_color[2])
            except Exception as e:
                 inverted_color = (255, 255, 255)
                 print(f"RENDER_DEBUG: Error sampling pixel for T2 at ({sample_x}, {sample_y}): {e}. Defaulting color.")

            try:
                draw.text((line_left_x, y_offset), line, fill=inverted_color, font=body_font)
            except Exception as e:
                 print(f"RENDER_DEBUG: Error drawing Text 2 line '{line}': {e}")

            y_offset += body_line_height # Move down

        print("RENDER_DEBUG: Image rendering complete. Returning image object.")
        return img # Return the Pillow Image object

    except FileNotFoundError:
        print(f"RENDER_DEBUG: ERROR: Poster image not found at {full_poster_path}")
        return None
    except Exception as e:
        print(f"RENDER_DEBUG: ERROR during image rendering for {poster_path}: {e}")
        return None


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

    return render_template('round_results.html',
                           game_state=game_state, current_player=current_player,
                           results=results, sorted_players=sorted_players,
                           is_game_over=is_game_over)


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