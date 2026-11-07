import { useEffect, useState, type ReactNode } from 'react'
import type { Board, SetupChoices } from './api'
import { WORDS } from './words'
import './child.css'

// 家族の設定。問いは選ぶ形で、自由な文字は子のあだ名の1欄だけ。あだ名はサーバーで呼び名として持ち、
// AIへ送る前に［子］へ伏せる（choices には入れない）。入院した日はAIに渡さない。
// 連絡する相手は、初めから3つとも選んだ形（触らずに進んでも、下書きが消えないように）。
type Option = string | { value: string; label: string }
const valueOf = (option: Option) => (typeof option === 'string' ? option : option.value)
const labelOf = (option: Option) => (typeof option === 'string' ? option : option.label)

const ALL_CONTACTS = WORDS.setup.contactOptions.map(valueOf)
// サーバーの NICKNAME_MAX_CHARS と同じ。
const NICKNAME_MAX = 8
// あだ名は台帳の文の中を部分一致で伏せるので、1文字・条件・日付・医療・症状の語と重なる語は使えない。
// サーバー（guards.py の NICKNAME_BLOCKED）と同じ表（test_child_facts が突き合わせる）。最後はサーバーが決める。
const NICKNAME_BLOCKED = [
  'なら', 'かも', 'かな', '予定', '見込み', 'しだい', '次第', '退院', '入院', '検査', '採血',
  '確定', '候補', '延長', '迎え', '先生', '病院', '病棟', '保育園', '職場', '家族', '明日', '今日',
  '夫', '母', '父', '薬', '量', 'mg', 'ミリグラム', '投与', '中止', '増やす', '減らす', '服用',
  '点滴', '副作用', '解熱', 'crp', '明後日', 'あさって', 'あした', '昨日', '今夜', '今晩', '今朝',
  '今週', '来週', '再来週', '週末', 'けいれん', '痙攣', 'ひきつけ', 'ぐったり', '呼吸', '意識',
  '顔色', '嘔吐', '出血', '血便', '下痢', '発疹', '湿疹', 'じんましん', '腫れ', 'むくみ', '痛い',
  '痛み', '震え', 'ふるえ', '高熱', '咳', 'せき', 'おしっこ', 'ゼーゼー', 'チアノーゼ', 'ゼイゼイ',
  'ヒューヒュー', '真っ青', '青白', '唇が紫', '血を吐', '目を覚まさない', '呼びかけ',
]
export function nicknameAllowed(raw: string): boolean {
  const name = raw.trim()
  if (!name) return true
  if (name.length < 2 || /[0-9０-９日月時曜]/.test(name)) return false
  const folded = name.normalize('NFKC').toLowerCase()
  return !NICKNAME_BLOCKED.some((word) => word.includes(folded) || folded.includes(word))
}
// 外した2問（一緒に暮らす大人・家でお世話が必要な子）は持たない。古い台帳の答えは startFrom で残る。
const EMPTY: SetupChoices = {
  parent: null,
  school: null,
  work: null,
  errand: null,
  errand_time: null,
  bedside: null,
  supports: [],
  no_support: false,
  contacts: ALL_CONTACTS,
  nickname: '',
  admitted_on: '',
}

// 前に保存した内容に連絡する相手が無ければ（この欄を足す前の家庭）、3つとも選んだ形で開く。
function startFrom(initial: SetupChoices | null): SetupChoices {
  if (!initial) return EMPTY
  return { ...EMPTY, ...initial, contacts: initial.contacts ?? ALL_CONTACTS }
}

// 直すときの初めの内容。あだ名と入院した日は choices の外にあるので、ここで混ぜる。
export function setupInitial(board: Board): SetupChoices | null {
  const child = { nickname: board.nickname ?? '', admitted_on: board.admitted_on ?? '' }
  if (board.profile.choices) return { ...board.profile.choices, ...child }
  return board.nickname || board.admitted_on ? { ...EMPTY, ...child } : null
}

type Single = Exclude<
  keyof SetupChoices,
  'supports' | 'no_support' | 'contacts' | 'nickname' | 'admitted_on'
>

function CheckMark() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 20 20">
      <path
        d="M4 10.5l4 4 8-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function SetupScreen({
  initial,
  editing = false,
  busy,
  message,
  onSave,
  onLater,
  footer,
}: {
  initial: SetupChoices | null
  editing?: boolean
  busy: boolean
  message: string | null
  onSave: (choices: SetupChoices) => void
  // 最初の画面では、そこまで選んだ内容（連絡する相手を含む）も渡す。
  onLater: (choices: SetupChoices) => void
  // 1ページ目の一番下に置くもの（最初の画面の「今ある記録から始める」）。
  footer?: ReactNode
}) {
  const words = WORDS.setup
  const [page, setPage] = useState<1 | 2>(1)
  const [choices, setChoices] = useState<SetupChoices>(() => startFrom(initial))
  const nicknameBad = !nicknameAllowed(choices.nickname ?? '')

  // ページを移ったら頭から見せる。ボタンは一番下にあるので、そのままだと次のページの下から始まる。
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [page])

  const pick = (key: Single, value: string) =>
    setChoices((current) => {
      const chosen = current[key] === value ? null : value
      const next = { ...current, [key]: chosen }
      // 家のことが中心なら、職場への文は作らない。チェックを外すだけなので、押せば戻せる。
      if (key === 'work' && chosen === words.homeWork) {
        next.contacts = (current.contacts ?? ALL_CONTACTS).filter(
          (name) => name !== words.workplace,
        )
      }
      // 家のことが中心から別の答えに変えたら、外した職場を戻す（QA Test の P2）。
      if (key === 'work' && current.work === words.homeWork && chosen !== words.homeWork) {
        const contacts = current.contacts ?? ALL_CONTACTS
        if (!contacts.includes(words.workplace)) next.contacts = [...contacts, words.workplace]
      }
      return next
    })

  // 問いの下の1行は「この答えで変わること」。札と台帳の値は分ける（data-setup は値）。
  const single = (
    key: Single,
    title: string,
    detail: string,
    options: readonly Option[],
    even = false,
  ) => (
    <fieldset className="setup-field" key={key}>
      <legend>{title}</legend>
      <p className="detail setup-detail">{detail}</p>
      <div className={even ? 'setup-options even' : 'setup-options'}>
        {options.map((option) => {
          const value = valueOf(option)
          return (
            <button
              type="button"
              key={value}
              className="choice"
              aria-pressed={choices[key] === value}
              data-setup={`${key}:${value}`}
              onClick={() => pick(key, value)}
            >
              {labelOf(option)}
            </button>
          )
        })}
      </div>
    </fieldset>
  )

  const toggleSupport = (name: string) =>
    setChoices((current) => ({
      ...current,
      no_support: false,
      supports: current.supports.includes(name)
        ? current.supports.filter((entry) => entry !== name)
        : [...current.supports, name],
    }))

  const contacts = choices.contacts ?? ALL_CONTACTS
  const toggleContact = (name: string) =>
    setChoices((current) => {
      const now = current.contacts ?? ALL_CONTACTS
      return {
        ...current,
        contacts: now.includes(name)
          ? now.filter((entry) => entry !== name)
          : ALL_CONTACTS.filter((entry) => entry === name || now.includes(entry)),
      }
    })

  return (
    <div className="with-dock setup-page">
      <main className="setup-screen" data-setup-screen data-setup-step={page}>
        <p className="detail">{editing ? words.stepEdit(page) : words.step(page)}</p>
        <div
          className="setup-progress"
          role="progressbar"
          aria-label={words.progress}
          aria-valuemin={0}
          aria-valuemax={2}
          aria-valuenow={page}
          data-setup-progress
        >
          <span style={{ width: page === 1 ? '50%' : '100%' }} />
        </div>
        <h1>{page === 1 ? words.title1 : words.title2}</h1>
        <div className="setup-lead">
          <p className="detail">{words.lead}</p>
          <p className="detail">{words.noRealNames}</p>
        </div>
        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}
        {page === 1 ? (
          <>
            {/* どちらも空でよい。あだ名が空なら、画面では「お子さん」。 */}
            <fieldset className="setup-field" data-nickname>
              <legend>{words.nickname}</legend>
              <p className="detail setup-detail">{words.nicknameDetail}</p>
              <input
                type="text"
                className="setup-input"
                maxLength={NICKNAME_MAX}
                autoComplete="off"
                aria-label={words.nickname}
                value={choices.nickname ?? ''}
                onChange={(event) =>
                  setChoices((current) => ({ ...current, nickname: event.target.value }))
                }
              />
              {nicknameBad && (
                <p className="detail" role="alert" data-nickname-refused>
                  {words.nicknameRefused}
                </p>
              )}
            </fieldset>
            <fieldset className="setup-field" data-admitted>
              <legend>{words.admittedOn}</legend>
              <p className="detail setup-detail">{words.admittedOnDetail}</p>
              <input
                type="date"
                className="setup-input"
                aria-label={words.admittedOn}
                value={choices.admitted_on ?? ''}
                onChange={(event) =>
                  setChoices((current) => ({ ...current, admitted_on: event.target.value }))
                }
              />
            </fieldset>
            {single('parent', words.parent, words.parentDetail, words.parentOptions, true)}
            {single('school', words.school, words.schoolDetail, words.schoolOptions, true)}
            {single('work', words.work, words.workDetail, words.workOptions)}
            <fieldset className="setup-field" data-contacts>
              <legend>{words.contacts}</legend>
              <p className="detail setup-detail">{words.contactsDetail}</p>
              <div className="setup-options">
                {words.contactOptions.map(({ value, label }) => (
                  <button
                    type="button"
                    key={value}
                    role="checkbox"
                    className="choice contact"
                    aria-checked={contacts.includes(value)}
                    data-contact={value}
                    data-setup={`contacts:${value}`}
                    onClick={() => toggleContact(value)}
                  >
                    <span className="contact-box">
                      {contacts.includes(value) && <CheckMark />}
                    </span>
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
          </>
        ) : (
          <>
            {single('errand', words.errand, words.errandDetail, words.errandOptions)}
            {choices.errand && choices.errand !== words.nobody
              ? single(
                  'errand_time',
                  words.errandTime,
                  words.errandTimeDetail,
                  words.errandTimeOptions,
                )
              : null}
            {single('bedside', words.bedside, words.bedsideDetail, words.yesNo)}
            <fieldset className="setup-field">
              <legend>{words.supports}</legend>
              <p className="detail setup-detail">{words.supportsDetail}</p>
              <div className="setup-options">
                {words.supportOptions.map((option) => (
                  <button
                    type="button"
                    key={option}
                    className="choice"
                    aria-pressed={choices.supports.includes(option)}
                    data-setup={`supports:${option}`}
                    onClick={() => toggleSupport(option)}
                  >
                    {option}
                  </button>
                ))}
                <button
                  type="button"
                  className="choice"
                  aria-pressed={choices.no_support}
                  data-setup="supports:なし"
                  onClick={() =>
                    setChoices((current) => ({
                      ...current,
                      supports: [],
                      no_support: !current.no_support,
                    }))
                  }
                >
                  {words.none}
                </button>
              </div>
            </fieldset>
          </>
        )}
      </main>
      {/* 図のとおり、ボタンは選ぶ欄の続き。欄が画面より長い間は、画面の下に張り付けて見せる。 */}
      <div className="dock setup-actions" data-setup-actions>
        {page === 1 ? (
          <>
            {/* 押せない理由をボタンの近くにも出す。欄の下の理由は、画面の下からは見えない（2026-11-08 本人の実機）。
                読み上げは欄の下の1つ（role=alert）に任せ、ここは重ねない。 */}
            {nicknameBad && (
              <p className="detail" data-nickname-refused-near>
                {words.nicknameRefused}
              </p>
            )}
            <button
              type="button"
              className="primary"
              data-primary
              disabled={nicknameBad}
              onClick={() => setPage(2)}
            >
              {words.next}
            </button>
            <p className="detail setup-next">{words.nextLead(words.title2)}</p>
            {/* 直すときは2ページ目の［戻る］が1ページ目へ戻るので、1ページ目でだけ閉じる。 */}
            <button type="button" disabled={busy || nicknameBad} onClick={() => onLater(choices)}>
              {editing ? WORDS.add.back : words.later}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              data-primary
              disabled={busy}
              onClick={() => onSave(choices)}
            >
              {editing ? words.saveEdit : words.save}
            </button>
            <div className="dock-pair">
              <button type="button" onClick={() => setPage(1)}>
                {WORDS.add.back}
              </button>
              {!editing && (
                <button type="button" disabled={busy} onClick={() => onLater(choices)}>
                  {words.later}
                </button>
              )}
            </div>
          </>
        )}
      </div>
      {page === 1 && footer}
    </div>
  )
}
