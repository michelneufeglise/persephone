import React from 'react'
import {
  Sun, Moon, CloudSun, CloudMoon, Cloud, Cloudy, CloudRain, CloudDrizzle,
  CloudSnow, CloudLightning, CloudFog, Snowflake, Wind, Umbrella, Thermometer,
  ThermometerSun, ThermometerSnowflake, Droplets, Sunrise, Sunset, Rainbow, Tornado,
  Sparkle, Sparkles, Star, Heart, Flame, Zap, Bell, BellOff, Check, CheckCircle2,
  X, XCircle, AlertTriangle, AlertCircle, Info, HelpCircle, Ban, ShieldCheck,
  ArrowRight, ArrowLeft, ArrowUp, ArrowDown, TrendingUp, TrendingDown, TrendingUpDown,
  Clock, Calendar, CalendarClock, Timer, Hourglass, MapPin, Compass, Globe, Map,
  Home, Building2, Car, Plane, Train, Bike, Ship, Rocket, Bus,
  Book, BookOpen, GraduationCap, Newspaper, PenTool, Pencil, Lightbulb, Brain,
  Music, Music2, Guitar, Headphones, Mic, Volume2, Film, Camera, Image, Video, Tv,
  Coffee, Utensils, Pizza, Beer, Wine, Cake, Apple, Cookie, Egg, Fish,
  Dog, Cat, Bird, Rabbit, Bug, Leaf, TreePine, TreeDeciduous, Flower, Flower2,
  Mountain, Waves, Trees,
  Laptop, Monitor, Smartphone, Tablet, Keyboard, Mouse, Watch, Printer, Server,
  Cpu, HardDrive, MemoryStick, Cable, Wifi, Bluetooth,
  Code, Terminal, Bug as BugIcon, GitBranch, GitMerge, GitPullRequest, Github,
  Database, Cloud as CloudIcon, Package, Boxes, Layers,
  Mail, MessageCircle, MessageSquare, Send, Phone, Users, User, UserPlus,
  ShoppingCart, ShoppingBag, CreditCard, Wallet, DollarSign, Euro, PoundSterling, Bitcoin, Coins, Receipt, PiggyBank, Banknote,
  BarChart3, LineChart, PieChart, Activity, Gauge,
  Search, Filter, Settings, Wrench, Hammer, Ruler, Scissors, Paperclip, Bookmark,
  Trash2, Save, Download, Upload, Share2, Link, Copy, Eye, EyeOff, Lock, Unlock, Key,
  Trophy, Award, Medal, Crown, Gift, PartyPopper, Cake as CakeIcon,
  Baby, PersonStanding, Dumbbell, Bike as BikeIcon, HeartPulse, Stethoscope, Pill, Syringe, Cross,
  Palette, Paintbrush, Brush, Feather, Scroll, FileText, FolderOpen, Folder, Files,
  Puzzle, Dices, Gamepad2, Swords, Target, Flag,
  ThumbsUp, ThumbsDown, Smile, Frown, Laugh, Meh, Angry, Skull, Ghost,
  type LucideIcon,
} from 'lucide-react'

// Curated shortcode → icon map. Keep names short, hyphenated, memorable —
// the model needs to guess these from context, so favour the intuitive one.
const ICONS: Record<string, LucideIcon> = {
  // Weather ─────────────────────────────────────────────────────────────
  sun: Sun, moon: Moon,
  'cloud-sun': CloudSun, 'partly-cloudy': CloudSun,
  'cloud-moon': CloudMoon,
  cloud: Cloud, cloudy: Cloudy, overcast: Cloudy,
  'cloud-rain': CloudRain, rain: CloudRain, rainy: CloudRain,
  drizzle: CloudDrizzle,
  'cloud-snow': CloudSnow, snow: CloudSnow, snowy: CloudSnow,
  snowflake: Snowflake,
  'cloud-lightning': CloudLightning, thunder: CloudLightning, storm: CloudLightning, lightning: CloudLightning,
  fog: CloudFog, mist: CloudFog, foggy: CloudFog,
  wind: Wind, windy: Wind, breeze: Wind,
  umbrella: Umbrella,
  temperature: Thermometer, thermometer: Thermometer,
  hot: ThermometerSun, warm: ThermometerSun,
  cold: ThermometerSnowflake, chilly: ThermometerSnowflake,
  humidity: Droplets, water: Droplets, wet: Droplets,
  sunrise: Sunrise, sunset: Sunset,
  rainbow: Rainbow, tornado: Tornado,

  // Feedback / status ───────────────────────────────────────────────────
  sparkle: Sparkle, sparkles: Sparkles,
  star: Star, heart: Heart,
  fire: Flame, flame: Flame, hot2: Flame,
  bolt: Zap, zap: Zap, energy: Zap,
  bell: Bell, notification: Bell, 'bell-off': BellOff, mute: BellOff,
  check: Check, done: Check,
  success: CheckCircle2, ok: CheckCircle2,
  x: X, cancel: X, close: X,
  fail: XCircle, error: XCircle,
  warning: AlertTriangle, caution: AlertTriangle,
  alert: AlertCircle,
  info: Info, help: HelpCircle, question: HelpCircle,
  ban: Ban, forbidden: Ban,
  shield: ShieldCheck, secure: ShieldCheck,

  // Arrows & trends ─────────────────────────────────────────────────────
  right: ArrowRight, 'arrow-right': ArrowRight,
  left: ArrowLeft, 'arrow-left': ArrowLeft,
  up: ArrowUp, 'arrow-up': ArrowUp,
  down: ArrowDown, 'arrow-down': ArrowDown,
  'trend-up': TrendingUp, up2: TrendingUp,
  'trend-down': TrendingDown, down2: TrendingDown,
  volatile: TrendingUpDown,

  // Time & place ────────────────────────────────────────────────────────
  clock: Clock, time: Clock,
  calendar: Calendar, date: Calendar,
  schedule: CalendarClock, 'calendar-clock': CalendarClock,
  timer: Timer, hourglass: Hourglass,
  pin: MapPin, location: MapPin, place: MapPin,
  compass: Compass,
  globe: Globe, world: Globe, earth: Globe,
  map: Map,

  // Travel & places ─────────────────────────────────────────────────────
  home: Home, house: Home,
  building: Building2, office: Building2,
  car: Car, drive: Car,
  plane: Plane, flight: Plane, travel: Plane,
  train: Train, bike: Bike, cycle: Bike, ship: Ship, boat: Ship, rocket: Rocket, bus: Bus,

  // Learning & thought ──────────────────────────────────────────────────
  book: Book, books: BookOpen,
  study: GraduationCap, learn: GraduationCap, graduate: GraduationCap,
  news: Newspaper, article: Newspaper,
  pen: PenTool, pencil: Pencil, write: Pencil,
  idea: Lightbulb, insight: Lightbulb, tip: Lightbulb,
  brain: Brain, think: Brain,

  // Media & sound ───────────────────────────────────────────────────────
  music: Music, note: Music, song: Music2,
  guitar: Guitar, headphones: Headphones, mic: Mic, sound: Volume2, volume: Volume2,
  film: Film, movie: Film, camera: Camera, image: Image, picture: Image, video: Video, tv: Tv,

  // Food & drink ────────────────────────────────────────────────────────
  coffee: Coffee, tea: Coffee,
  food: Utensils, eat: Utensils, meal: Utensils,
  pizza: Pizza, beer: Beer, wine: Wine, cake: Cake, birthday: CakeIcon,
  apple: Apple, cookie: Cookie, egg: Egg, fish: Fish,

  // Nature ──────────────────────────────────────────────────────────────
  dog: Dog, cat: Cat, bird: Bird, rabbit: Rabbit, bug: Bug,
  leaf: Leaf, plant: Leaf,
  tree: TreePine, pine: TreePine, oak: TreeDeciduous, forest: Trees,
  flower: Flower, blossom: Flower2,
  mountain: Mountain, hill: Mountain,
  wave: Waves, ocean: Waves, sea: Waves,

  // Tech & devices ──────────────────────────────────────────────────────
  laptop: Laptop, computer: Laptop, monitor: Monitor, screen: Monitor,
  phone: Smartphone, mobile: Smartphone, tablet: Tablet,
  keyboard: Keyboard, mouse: Mouse, watch: Watch, printer: Printer,
  server: Server, cpu: Cpu, chip: Cpu,
  'hard-drive': HardDrive, disk: HardDrive, ram: MemoryStick, memory: MemoryStick,
  cable: Cable, wifi: Wifi, bluetooth: Bluetooth,

  // Dev ─────────────────────────────────────────────────────────────────
  code: Code, terminal: Terminal, shell: Terminal, cli: Terminal,
  debug: BugIcon,
  branch: GitBranch, merge: GitMerge, pr: GitPullRequest, github: Github,
  database: Database, db: Database,
  cloud2: CloudIcon,
  package: Package, box: Package, boxes: Boxes, layers: Layers,

  // Comms ───────────────────────────────────────────────────────────────
  mail: Mail, email: Mail, envelope: Mail,
  chat: MessageCircle, message: MessageSquare,
  send: Send,
  call: Phone, tel: Phone,
  users: Users, people: Users, team: Users, user: User, person: User,
  'user-plus': UserPlus, invite: UserPlus,

  // Money & commerce ────────────────────────────────────────────────────
  cart: ShoppingCart, shop: ShoppingBag, bag: ShoppingBag,
  card: CreditCard, wallet: Wallet,
  dollar: DollarSign, usd: DollarSign,
  euro: Euro, eur: Euro,
  pound: PoundSterling, gbp: PoundSterling,
  bitcoin: Bitcoin, btc: Bitcoin,
  coins: Coins, receipt: Receipt,
  save2: PiggyBank, savings: PiggyBank,
  cash: Banknote, money: Banknote,

  // Analytics ───────────────────────────────────────────────────────────
  chart: BarChart3, bar: BarChart3,
  line: LineChart, pie: PieChart, activity: Activity, gauge: Gauge, meter: Gauge,

  // Tools & UI ──────────────────────────────────────────────────────────
  search: Search, find: Search, filter: Filter,
  settings: Settings, gear: Settings, cog: Settings,
  wrench: Wrench, tool: Wrench, hammer: Hammer, ruler: Ruler,
  scissors: Scissors, cut: Scissors, clip: Paperclip, bookmark: Bookmark,
  trash: Trash2, delete: Trash2,
  save: Save, download: Download, upload: Upload,
  share: Share2, link: Link, copy: Copy,
  eye: Eye, view: Eye, hide: EyeOff,
  lock: Lock, unlock: Unlock, key: Key,

  // Celebration & achievement ───────────────────────────────────────────
  trophy: Trophy, win: Trophy,
  award: Award, badge: Award,
  medal: Medal, crown: Crown,
  gift: Gift, present: Gift, party: PartyPopper, celebrate: PartyPopper,

  // Body & health ───────────────────────────────────────────────────────
  baby: Baby, walk: PersonStanding, standing: PersonStanding,
  gym: Dumbbell, workout: Dumbbell, exercise: Dumbbell,
  cycling: BikeIcon,
  pulse: HeartPulse, health: HeartPulse,
  doctor: Stethoscope, medical: Stethoscope,
  pill: Pill, medicine: Pill,
  syringe: Syringe, shot: Syringe,
  cross: Cross, plus: Cross,

  // Art & files ─────────────────────────────────────────────────────────
  palette: Palette, art: Palette,
  paint: Paintbrush, brush: Brush, feather: Feather, scroll: Scroll,
  file: FileText, doc: FileText, folder: Folder, 'folder-open': FolderOpen, files: Files,

  // Games ───────────────────────────────────────────────────────────────
  puzzle: Puzzle, dice: Dices, dices: Dices,
  game: Gamepad2, gaming: Gamepad2,
  swords: Swords, fight: Swords, battle: Swords,
  target: Target, goal: Target, aim: Target,
  flag: Flag,

  // Emotion ─────────────────────────────────────────────────────────────
  up3: ThumbsUp, thumbsup: ThumbsUp, like: ThumbsUp,
  down3: ThumbsDown, thumbsdown: ThumbsDown, dislike: ThumbsDown,
  smile: Smile, happy: Smile,
  frown: Frown, sad: Frown,
  laugh: Laugh, lol: Laugh,
  meh: Meh, neutral: Meh,
  angry: Angry,
  skull: Skull, dead: Skull,
  ghost: Ghost, boo: Ghost,
}

// Ordered alias list surfaced in the model hint. Keep this compact — models
// pattern-match on the ones they see. Add more via ICONS above.
export const ICON_HINT_LIST = [
  'sun', 'moon', 'cloud', 'cloud-sun', 'cloud-rain', 'cloud-snow',
  'cloud-lightning', 'wind', 'snowflake', 'umbrella', 'thermometer',
  'hot', 'cold', 'sunrise', 'sunset', 'rainbow',
  'star', 'heart', 'fire', 'bolt', 'sparkles',
  'check', 'x', 'warning', 'info', 'help', 'shield',
  'trend-up', 'trend-down',
  'clock', 'calendar', 'pin', 'globe', 'map',
  'home', 'car', 'plane', 'train', 'ship',
  'book', 'idea', 'brain', 'pen',
  'music', 'film', 'camera', 'headphones',
  'coffee', 'food', 'pizza',
  'dog', 'cat', 'bird', 'leaf', 'tree', 'flower', 'mountain', 'wave',
  'laptop', 'phone', 'wifi',
  'code', 'terminal', 'branch', 'github', 'database',
  'mail', 'chat', 'phone', 'users',
  'cart', 'dollar', 'euro', 'coins', 'chart', 'pie',
  'search', 'settings', 'lock', 'key',
  'trophy', 'award', 'gift',
  'health', 'medical', 'pill',
  'palette', 'file', 'folder',
  'target', 'flag', 'game',
  'like', 'dislike', 'smile', 'sad',
]

// Safe lookup that ignores inherited props like `constructor`, `toString`, etc.
function lookupIcon(name: string): LucideIcon | undefined {
  const key = name.toLowerCase()
  if (!Object.prototype.hasOwnProperty.call(ICONS, key)) return undefined
  const candidate = ICONS[key]
  return typeof candidate === 'function' || typeof candidate === 'object'
    ? candidate
    : undefined
}

/** Renders one inline icon aligned with surrounding text. */
export function InlineIcon({ name }: { name: string }) {
  const Icon = lookupIcon(name)
  if (!Icon) return <span>:{name}:</span>
  return (
    <Icon
      aria-hidden
      className="inline-block align-[-0.15em] mx-[0.15em] w-[1.05em] h-[1.05em] text-[var(--accent)]"
      strokeWidth={2}
    />
  )
}

// Only match single-line lowercase alnum+hyphen, 2..24 chars, letter-first.
const ICON_RE = /:([a-z][a-z0-9-]{1,23}):/g

/**
 * Walk a react-markdown children tree and, in every text node, replace
 * `:icon-name:` shortcodes with <InlineIcon> elements. Non-text nodes pass
 * through unchanged. Wrapped in a try/catch so any regression falls back to
 * the original children instead of crashing the renderer.
 */
export function withInlineIcons(children: React.ReactNode): React.ReactNode {
  try {
    return transform(children, { n: 0 })
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[InlineIcons] transform failed, using original children:', err)
    return children
  }
}

function transform(children: React.ReactNode, counter: { n: number }): React.ReactNode {
  const out: React.ReactNode[] = []
  const nextKey = () => `ii-${counter.n++}`

  React.Children.forEach(children, (child) => {
    if (typeof child === 'string') {
      out.push(...splitText(child, nextKey))
      return
    }
    if (typeof child === 'number' || typeof child === 'boolean' || child == null) {
      out.push(child)
      return
    }
    if (React.isValidElement(child)) {
      const el = child as React.ReactElement<{ children?: React.ReactNode }>
      const kids = el.props?.children
      if (kids === undefined || kids === null) {
        out.push(child)
      } else {
        out.push(React.cloneElement(el, { key: el.key ?? nextKey() }, transform(kids, counter)))
      }
      return
    }
    out.push(child)
  })

  return out.length === 1 ? out[0] : out
}

function splitText(text: string, nextKey: () => string): React.ReactNode[] {
  if (!text.includes(':')) return [text]
  const parts: React.ReactNode[] = []
  let last = 0
  // Build a fresh regex each call — avoids sharing lastIndex across concurrent
  // renders or throwing if this file gets tree-shaken oddly.
  const re = new RegExp(ICON_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    // Runaway-safe: if the regex somehow matched zero characters, advance.
    if (m[0].length === 0) { re.lastIndex++; continue }
    const name = m[1]
    if (!Object.prototype.hasOwnProperty.call(ICONS, name)) continue
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(<InlineIcon key={nextKey()} name={name} />)
    last = m.index + m[0].length
  }
  if (last === 0) return [text]
  if (last < text.length) parts.push(text.slice(last))
  return parts
}
