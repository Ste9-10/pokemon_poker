from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, request, render_template, redirect, url_for, session, flash, send_file, jsonify
import os
import random
import threading
import socket

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey_pokemonpoker_12345')

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
AVATAR_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'avatars')
DEFAULT_AVATARS_FOLDER = os.path.join(BASE_DIR, 'static', 'images', 'avatars')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = 2 * 1024 * 1024
DEFAULT_AVATARS = ['pikachu.png', 'charizard.png', 'blastoise.png', 'venusaur.png', 'jigglypuff.png', 'mewtwo.png']
TURN_DURATION_SECONDS = int(os.environ.get('TURN_DURATION_SECONDS', '30'))

os.makedirs(AVATAR_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DEFAULT_AVATARS_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = AVATAR_UPLOAD_FOLDER

CREDENTIALS_FILE = os.path.join(BASE_DIR, 'credenziali.txt')
credentials_db = {}
credentials_loaded = False

games = {}
rooms = {}
turn_timers = {}
game_lock = threading.RLock()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_avatar_filename(username, filename):
    timestamp = int(datetime.now().timestamp())
    ext = filename.rsplit('.', 1)[1].lower()
    return f"{username}_{timestamp}.{ext}"


def is_valid_default_avatar(avatar):
    return avatar in DEFAULT_AVATARS and os.path.exists(os.path.join(DEFAULT_AVATARS_FOLDER, avatar))


def load_credentials_from_file():
    creds = {}
    if not os.path.exists(CREDENTIALS_FILE):
        return creds

    with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.count(':') < 1:
                continue
            parts = line.split(':')
            username = parts[0]
            password = parts[1]
            avatar = parts[2] if len(parts) >= 3 else 'pikachu.png'

            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar)
            default_path = os.path.join(DEFAULT_AVATARS_FOLDER, avatar)
            if avatar not in DEFAULT_AVATARS and not os.path.exists(upload_path) and not os.path.exists(default_path):
                avatar = 'pikachu.png'

            creds[username] = {'password': password, 'avatar': avatar}
    return creds


def load_credentials():
    global credentials_loaded, credentials_db
    if not credentials_loaded:
        try:
            credentials_db = load_credentials_from_file()
        except Exception:
            credentials_db = {}
        credentials_loaded = True
    return credentials_db.copy()


def save_credentials(creds):
    global credentials_db, credentials_loaded
    credentials_loaded = True
    credentials_db = creds.copy()
    try:
        with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
            for username, data in credentials_db.items():
                line = f"{username}:{data['password']}:{data.get('avatar', 'pikachu.png')}\n"
                f.write(line)
    except Exception:
        pass


class PokemonDeck:
    suits = ['spades', 'hearts', 'clubs', 'diamonds']
    values = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

    def __init__(self):
        self.cards = [
            {'suit': s, 'value': v, 'pokemon': self.get_pokemon(s, v)}
            for s in self.suits for v in self.values
        ]
        self.shuffle()

    def get_pokemon(self, suit, value):
        pokemon = {
            'spades': ['Charmander', 'Vulpix', 'Growlithe', 'Ponyta', 'Magmar', 'Arcanine', 'Rapidash', 'Charmeleon', 'Charizard', 'Moltres', 'Entei', 'Torchic', 'Blaziken'],
            'hearts': ['Squirtle', 'Psyduck', 'Poliwag', 'Tentacool', 'Magikarp', 'Staryu', 'Goldeen', 'Wartortle', 'Blastoise', 'Articuno', 'Suicune', 'Mudkip', 'Swampert'],
            'clubs': ['Bulbasaur', 'Oddish', 'Bellsprout', 'Exeggcute', 'Tangela', 'Weepinbell', 'Victreebel', 'Ivysaur', 'Venusaur', 'Celebi', 'Raikou', 'Treecko', 'Sceptile'],
            'diamonds': ['Pikachu', 'Magnemite', 'Voltorb', 'Electabuzz', 'Jolteon', 'Magneton', 'Electrode', 'Raichu', 'Zapdos', 'Ampharos', 'Manectric', 'Plusle', 'Minun']
        }
        idx = self.values.index(value) % len(pokemon[suit])
        return pokemon[suit][idx]

    def shuffle(self):
        random.shuffle(self.cards)

    def draw(self):
        return self.cards.pop() if self.cards else None

    def draw_multiple(self, count):
        cards = []
        for _ in range(count):
            card = self.draw()
            if card:
                cards.append(card)
        return cards


def evaluate_hand(hand):
    if not hand or len(hand) != 5:
        return 0

    values = [card['value'] for card in hand]
    suits = [card['suit'] for card in hand]
    value_map = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
    numeric_values = sorted([value_map[v] for v in values], reverse=True)

    value_counts = {}
    for v in numeric_values:
        value_counts[v] = value_counts.get(v, 0) + 1
    counts = sorted(value_counts.values(), reverse=True)

    is_flush = len(set(suits)) == 1
    unique_values = sorted(set(numeric_values), reverse=True)
    is_regular_straight = len(unique_values) == 5 and unique_values == list(range(unique_values[0], unique_values[0] - 5, -1))
    is_wheel_straight = unique_values == [14, 5, 4, 3, 2]
    is_straight = is_regular_straight or is_wheel_straight
    straight_high = 5 if is_wheel_straight else (unique_values[0] if is_straight else 0)

    if is_flush:
        if unique_values == [14, 13, 12, 11, 10]:
            return 10000
        if is_straight:
            return 9000 + straight_high
        return 6000 + sum(numeric_values)

    if is_straight:
        return 5000 + straight_high
    if counts == [4, 1]:
        return 8000 + max(k for k, v in value_counts.items() if v == 4)
    if counts == [3, 2]:
        return 7000 + max(k for k, v in value_counts.items() if v == 3)
    if counts == [3, 1, 1]:
        return 4000 + max(k for k, v in value_counts.items() if v == 3)
    if counts == [2, 2, 1]:
        pairs = [k for k, v in value_counts.items() if v == 2]
        return 3000 + max(pairs) * 100 + min(pairs)
    if counts == [2, 1, 1, 1]:
        pair = next(k for k, v in value_counts.items() if v == 2)
        return 2000 + pair
    return 1000 + sum(numeric_values)


def get_hand_name(score):
    if score >= 10000:
        return 'Scala Reale'
    if score >= 9000:
        return 'Scala Colore'
    if score >= 8000:
        return 'Poker'
    if score >= 7000:
        return 'Full'
    if score >= 6000:
        return 'Colore'
    if score >= 5000:
        return 'Scala'
    if score >= 4000:
        return 'Tris'
    if score >= 3000:
        return 'Doppia Coppia'
    if score >= 2000:
        return 'Coppia'
    return 'Carta Alta'


def cleanup_timer(game_id):
    timer = turn_timers.pop(game_id, None)
    if timer:
        try:
            timer.cancel()
        except Exception:
            pass


def get_next_pending_player_index(game, start_index=None):
    if not game['players']:
        return None

    total = len(game['players'])
    start = game['current_player'] if start_index is None else start_index
    for step in range(1, total + 1):
        idx = (start + step) % total
        player = game['players'][idx]
        if not game['players_ready'].get(player, False):
            return idx
    return None


def evaluate_round(game):
    cleanup_timer(game.get('id'))

    round_scores = {player: evaluate_hand(game['hands'][player]) for player in game['players']}
    best_score = max(round_scores.values())
    winners = [player for player, score in round_scores.items() if score == best_score]
    round_winner = winners[0]

    game['round_winner'] = round_winner
    game['round_winner_hand'] = get_hand_name(round_scores[round_winner])
    game['round_wins'][round_winner] += 1
    game['show_round_result'] = True
    game['phase'] = 'round_result'
    game['turn_start_time'] = None

    game['round_history'].append({
        'round': game['round'],
        'winner': round_winner,
        'hand_name': game['round_winner_hand'],
        'score': round_scores[round_winner],
        'tied_players': winners
    })


def _schedule_turn_timer(game_id):
    game = games.get(game_id)
    if not game or game.get('game_over') or game.get('phase') != 'cambio':
        return

    token = f"{datetime.now().timestamp()}_{random.random()}"
    game['timer_token'] = token

    def timer_callback():
        with game_lock:
            live_game = games.get(game_id)
            if not live_game:
                cleanup_timer(game_id)
                return
            if live_game.get('timer_token') != token:
                return
            if live_game.get('game_over') or live_game.get('phase') != 'cambio':
                cleanup_timer(game_id)
                return

            current_player_name = live_game['players'][live_game['current_player']]
            live_game['players_ready'][current_player_name] = True

            next_index = get_next_pending_player_index(live_game, live_game['current_player'])
            if next_index is None:
                evaluate_round(live_game)
                return

            live_game['current_player'] = next_index
            live_game['turn_start_time'] = datetime.now()
            cleanup_timer(game_id)
            _schedule_turn_timer(game_id)

    timer = threading.Timer(TURN_DURATION_SECONDS, timer_callback)
    timer.daemon = True
    turn_timers[game_id] = timer
    timer.start()


def start_turn_timer(game_id):
    with game_lock:
        cleanup_timer(game_id)
        _schedule_turn_timer(game_id)


def user_current_room_id(user):
    for room_id, room in rooms.items():
        if user in room['players']:
            return room_id
    return None


def user_current_game_id(user):
    for game_id, game in games.items():
        if user in game['players']:
            return game_id
    return None


def remove_player_from_game(game_id, username):
    game = games.get(game_id)
    if not game or username not in game['players']:
        return

    leaving_index = game['players'].index(username)
    game['players'].remove(username)
    game['hands'].pop(username, None)
    game['scores'].pop(username, None)
    game['round_wins'].pop(username, None)
    game['players_ready'].pop(username, None)

    room = rooms.get(game_id)
    if room and username in room['players']:
        room['players'].remove(username)
        if room['creator'] == username and room['players']:
            room['creator'] = room['players'][0]

    if not game['players']:
        cleanup_timer(game_id)
        games.pop(game_id, None)
        if room and not room['players']:
            rooms.pop(game_id, None)
        return

    if len(game['players']) == 1:
        sole_player = game['players'][0]
        game['winner'] = sole_player
        game['game_over'] = True
        game['phase'] = 'game_over'
        game['show_round_result'] = False
        cleanup_timer(game_id)
        return

    if game['current_player'] >= len(game['players']):
        game['current_player'] = 0
    elif leaving_index < game['current_player']:
        game['current_player'] -= 1

    if game['phase'] == 'cambio':
        current_player_name = game['players'][game['current_player']]
        if game['players_ready'].get(current_player_name, False):
            next_index = get_next_pending_player_index(game, game['current_player'])
            if next_index is None:
                evaluate_round(game)
                return
            game['current_player'] = next_index
        game['turn_start_time'] = datetime.now()
        start_turn_timer(game_id)

    if room and not room['players']:
        rooms.pop(game_id, None)


def remove_user_from_all_contexts(user):
    with game_lock:
        room_id = user_current_room_id(user)
        game_id = user_current_game_id(user)

        if game_id:
            remove_player_from_game(game_id, user)
        elif room_id and room_id in rooms:
            room = rooms[room_id]
            if user in room['players']:
                room['players'].remove(user)
                if room['creator'] == user and room['players']:
                    room['creator'] = room['players'][0]
                if not room['players']:
                    rooms.pop(room_id, None)
                    cleanup_timer(room_id)
                    games.pop(room_id, None)


@app.route('/')
def home():
    if 'user' in session:
        if 'avatar_selected' not in session:
            return redirect(url_for('change_avatar'))
        return redirect(url_for('lobby'))
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    creds = load_credentials()
    username = request.form['username'].strip()
    password = request.form['password']

    if username in creds and creds[username]['password'] == password:
        session['user'] = username
        session.pop('avatar_selected', None)
        return redirect(url_for('change_avatar'))

    flash('Credenziali errate!')
    return redirect(url_for('home'))


@app.route('/register', methods=['POST'])
def register():
    creds = load_credentials()
    username = request.form['username'].strip()
    password = request.form['password']

    if not username or not password:
        flash('Inserisci nome utente e password validi!')
        return redirect(url_for('home'))
    if username in creds:
        flash('Nome utente già esistente!')
        return redirect(url_for('home'))

    creds[username] = {'password': password, 'avatar': 'pikachu.png'}
    save_credentials(creds)
    session['user'] = username
    session.pop('avatar_selected', None)
    return redirect(url_for('change_avatar'))


@app.route('/change_avatar', methods=['GET', 'POST'])
def change_avatar():
    if 'user' not in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        if 'selected_avatar' in request.form:
            selected_avatar = request.form['selected_avatar']
            if is_valid_default_avatar(selected_avatar):
                username = session['user']
                creds = load_credentials()
                if username in creds:
                    creds[username]['avatar'] = selected_avatar
                    save_credentials(creds)
                    session['avatar_selected'] = True
                    return redirect(url_for('lobby'))

        if 'avatar' in request.files:
            file = request.files['avatar']
            if file.filename != '' and allowed_file(file.filename):
                file.stream.seek(0, os.SEEK_END)
                filesize = file.stream.tell()
                file.stream.seek(0)
                if filesize > MAX_FILE_SIZE:
                    flash('File troppo grande (max 2MB)')
                    return redirect(url_for('change_avatar'))

                username = session['user']
                filename = generate_avatar_filename(username, secure_filename(file.filename))
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                try:
                    file.save(filepath)
                    creds = load_credentials()
                    if username in creds:
                        old_avatar = creds[username]['avatar']
                        if old_avatar not in DEFAULT_AVATARS:
                            old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_avatar)
                            if os.path.exists(old_path):
                                os.remove(old_path)
                        creds[username]['avatar'] = filename
                        save_credentials(creds)
                        session['avatar_selected'] = True
                        return redirect(url_for('lobby'))
                except Exception:
                    flash("Errore durante il salvataggio dell'avatar")

        flash("Seleziona un avatar predefinito o carica un'immagine")

    return render_template('change_avatar.html', default_avatars=DEFAULT_AVATARS)


@app.route('/avatar/<filename>')
def get_avatar(filename):
    safe_name = secure_filename(filename)
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if not os.path.exists(safe_path):
        safe_path = os.path.join(DEFAULT_AVATARS_FOLDER, safe_name)
        if not os.path.exists(safe_path):
            safe_path = os.path.join(DEFAULT_AVATARS_FOLDER, 'pikachu.png')
    return send_file(safe_path)


@app.route('/lobby')
def lobby():
    if 'user' not in session or 'avatar_selected' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()
    available_rooms = []
    for room_id, room in rooms.items():
        if not room.get('game_started', False) and len(room['players']) < 10:
            available_rooms.append({
                'id': room_id,
                'name': room['name'],
                'creator': room['creator'],
                'players': len(room['players']),
                'max_players': 10
            })

    return render_template('lobby.html', user=user, creds=creds, available_rooms=available_rooms)


@app.route('/create_room', methods=['POST'])
def create_room():
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    existing_game = user_current_game_id(user)
    existing_room = user_current_room_id(user)
    if existing_game:
        return redirect(url_for('game', game_id=existing_game))
    if existing_room:
        return redirect(url_for('room', room_id=existing_room))

    room_name = request.form.get('room_name', f'Stanza di {user}').strip() or f'Stanza di {user}'
    room_id = str(random.randint(10000, 99999))
    while room_id in rooms:
        room_id = str(random.randint(10000, 99999))

    rooms[room_id] = {
        'id': room_id,
        'name': room_name,
        'creator': user,
        'players': [user],
        'game_started': False,
        'created_at': datetime.now()
    }
    return redirect(url_for('room', room_id=room_id))


@app.route('/join_room/<room_id>')
def join_room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    existing_game = user_current_game_id(user)
    existing_room = user_current_room_id(user)
    if existing_game:
        return redirect(url_for('game', game_id=existing_game))
    if existing_room and existing_room != room_id:
        return redirect(url_for('room', room_id=existing_room))

    if room_id not in rooms:
        flash('Stanza non trovata!')
        return redirect(url_for('lobby'))

    room = rooms[room_id]
    if room.get('game_started', False):
        flash('La partita è già iniziata!')
        return redirect(url_for('lobby'))
    if len(room['players']) >= 10:
        flash('Stanza piena!')
        return redirect(url_for('lobby'))

    if user not in room['players']:
        room['players'].append(user)
    return redirect(url_for('room', room_id=room_id))


@app.route('/room/<room_id>')
def room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()
    if room_id not in rooms:
        flash('Stanza non trovata!')
        return redirect(url_for('lobby'))

    room = rooms[room_id]
    if user not in room['players']:
        return redirect(url_for('join_room', room_id=room_id))
    if room.get('game_started', False) and room_id in games:
        return redirect(url_for('game', game_id=room_id))

    return render_template('room.html', room=room, user=user, creds=creds)


@app.route('/start_game/<room_id>', methods=['POST'])
def start_game(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    if room_id not in rooms:
        return redirect(url_for('lobby'))

    room = rooms[room_id]
    if user != room['creator']:
        flash('Solo il creatore può iniziare la partita!')
        return redirect(url_for('room', room_id=room_id))
    if len(room['players']) < 2:
        flash('Servono almeno 2 giocatori per iniziare!')
        return redirect(url_for('room', room_id=room_id))
    if room.get('game_started') and room_id in games:
        return redirect(url_for('game', game_id=room_id))

    players = room['players'].copy()
    random.shuffle(players)

    games[room_id] = {
        'id': room_id,
        'players': players,
        'deck': PokemonDeck(),
        'hands': {p: [] for p in players},
        'current_player': 0,
        'round': 1,
        'max_rounds': 5,
        'game_over': False,
        'winner': None,
        'scores': {p: 0 for p in players},
        'round_wins': {p: 0 for p in players},
        'phase': 'cambio',
        'players_ready': {p: False for p in players},
        'round_winner': None,
        'round_winner_hand': None,
        'show_round_result': False,
        'turn_start_time': datetime.now(),
        'round_history': [],
        'timer_token': None
    }

    for player in players:
        games[room_id]['hands'][player] = games[room_id]['deck'].draw_multiple(5)

    room['game_started'] = True
    start_turn_timer(room_id)
    return redirect(url_for('game', game_id=room_id))


@app.route('/kick_player/<room_id>/<player_name>', methods=['POST'])
def kick_player(room_id, player_name):
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Non autenticato'}), 401

    user = session['user']
    if room_id not in rooms:
        return jsonify({'success': False, 'error': 'Stanza non trovata'}), 404

    room = rooms[room_id]
    if user != room['creator']:
        return jsonify({'success': False, 'error': 'Solo il creatore può espellere i giocatori'}), 403
    if player_name == user:
        return jsonify({'success': False, 'error': 'Non puoi espellere te stesso'}), 400
    if player_name not in room['players']:
        return jsonify({'success': False, 'error': 'Giocatore non trovato nella stanza'}), 404

    if room.get('game_started') and room_id in games:
        with game_lock:
            remove_player_from_game(room_id, player_name)
    else:
        room['players'].remove(player_name)

    return jsonify({'success': True, 'message': f'{player_name} è stato espulso dalla stanza'})


@app.route('/game/<game_id>')
def game(game_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()
    if game_id not in games:
        return redirect(url_for('lobby'))

    game = games[game_id]
    if user not in game['players']:
        return redirect(url_for('lobby'))
    if game.get('game_over', False):
        return render_template('game_over.html', game=game, game_id=game_id, user=user, creds=creds)

    turn_time_left = TURN_DURATION_SECONDS
    if game.get('turn_start_time'):
        elapsed = (datetime.now() - game['turn_start_time']).total_seconds()
        turn_time_left = max(0, TURN_DURATION_SECONDS - elapsed)

    return render_template('game.html', game=game, game_id=game_id, user=user, creds=creds, turn_time_left=turn_time_left, turn_duration=TURN_DURATION_SECONDS)


@app.route('/action/<action>', methods=['POST'])
def game_action(action):
    if 'user' not in session or 'avatar_selected' not in session:
        return redirect(url_for('home'))

    user = session['user']
    payload = request.get_json(silent=True) or {}
    requested_game_id = request.args.get('game_id') or payload.get('game_id')

    if requested_game_id and requested_game_id in games and user in games[requested_game_id]['players']:
        game_id = requested_game_id
        game = games[game_id]
    else:
        game_id = user_current_game_id(user)
        game = games.get(game_id) if game_id else None

    if not game or game.get('game_over', False):
        return redirect(url_for('lobby'))

    with game_lock:
        if action == 'discard_selected':
            if game['phase'] == 'cambio' and game['players'][game['current_player']] == user:
                raw_indices = payload.get('selected_cards', [])
                selected_indices = sorted({int(i) for i in raw_indices if str(i).isdigit()})
                hand = game['hands'][user]
                selected_indices = [i for i in selected_indices if 0 <= i < len(hand)]

                new_hand = [card for i, card in enumerate(hand) if i not in selected_indices]
                new_hand.extend(game['deck'].draw_multiple(len(selected_indices)))
                game['hands'][user] = new_hand
                game['players_ready'][user] = True

                next_index = get_next_pending_player_index(game, game['current_player'])
                if next_index is None:
                    evaluate_round(game)
                else:
                    game['current_player'] = next_index
                    game['turn_start_time'] = datetime.now()
                    start_turn_timer(game_id)
            return jsonify({'success': True})

        if action == 'skip_discard':
            if game['phase'] == 'cambio' and game['players'][game['current_player']] == user:
                game['players_ready'][user] = True
                next_index = get_next_pending_player_index(game, game['current_player'])
                if next_index is None:
                    evaluate_round(game)
                else:
                    game['current_player'] = next_index
                    game['turn_start_time'] = datetime.now()
                    start_turn_timer(game_id)
            return jsonify({'success': True})

        if action == 'next_round':
            if game['show_round_result'] and game['phase'] == 'round_result':
                start_next_round(game, game_id)
            return jsonify({'success': True})

    return redirect(url_for('game', game_id=game_id))


def start_next_round(game, game_id):
    if game['round'] >= game['max_rounds']:
        max_wins = max(game['round_wins'].values())
        winners = [p for p, wins in game['round_wins'].items() if wins == max_wins]
        if len(winners) == 1:
            final_winner = winners[0]
        else:
            final_scores = {p: evaluate_hand(game['hands'][p]) for p in winners}
            final_winner = max(winners, key=lambda p: final_scores[p])

        for player in game['players']:
            game['scores'][player] = evaluate_hand(game['hands'][player])

        game['winner'] = final_winner
        game['game_over'] = True
        game['phase'] = 'game_over'
        cleanup_timer(game_id)
        return

    game['round'] += 1
    game['phase'] = 'cambio'
    game['current_player'] = 0
    game['players_ready'] = {p: False for p in game['players']}
    game['show_round_result'] = False
    game['round_winner'] = None
    game['round_winner_hand'] = None
    game['turn_start_time'] = datetime.now()
    game['deck'] = PokemonDeck()

    for player in game['players']:
        game['hands'][player] = game['deck'].draw_multiple(5)

    start_turn_timer(game_id)


@app.route('/game_status/<game_id>')
def game_status(game_id):
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404

    game = games[game_id]
    turn_time_left = TURN_DURATION_SECONDS
    if game.get('turn_start_time'):
        elapsed = (datetime.now() - game['turn_start_time']).total_seconds()
        turn_time_left = max(0, TURN_DURATION_SECONDS - elapsed)

    return jsonify({
        'game_over': game.get('game_over', False),
        'current_player': game.get('current_player', 0),
        'round': game.get('round', 1),
        'phase': game.get('phase', 'cambio'),
        'winner': game.get('winner'),
        'show_round_result': game.get('show_round_result', False),
        'round_winner': game.get('round_winner'),
        'turn_time_left': turn_time_left,
        'players_count': len(game.get('players', []))
    })


@app.route('/room_status/<room_id>')
def room_status(room_id):
    if room_id in rooms:
        room = rooms[room_id]
        return jsonify({'players': room['players'], 'game_started': room.get('game_started', False)})
    return jsonify({'error': 'Room not found'}), 404


@app.route('/leave_room/<room_id>', methods=['POST'])
def leave_room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    remove_user_from_all_contexts(session['user'])
    return redirect(url_for('lobby'))


@app.route('/logout')
def logout():
    user = session.get('user')
    if user:
        remove_user_from_all_contexts(user)
    session.pop('user', None)
    session.pop('avatar_selected', None)
    return redirect(url_for('home'))


def find_available_port():
    ports_to_try = [5001, 5002, 5003, 8000, 8080, 3000]
    for port in ports_to_try:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


if __name__ == '__main__':
    port = find_available_port()
    print(f'🚀 Starting Pokemon Poker server on port {port}')
    print(f'🌐 Access the game at: http://localhost:{port}')
    app.run(debug=True, host='0.0.0.0', port=port)
