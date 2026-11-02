// 画面の言葉の一覧。画面の文言はここに集める（使わない言葉の検査は backend/tests/test_screen_words.py）。

export const CONSENT_LINES = [
  'これは試作です。実在の人の名前や病名は入れないでください',
  '入れた文は、仕分けのため、Anthropic社（米国）のAIに送られます',
  'このボードのURLを知っている人は、中身を見られます。いつでも「このボードを消す」で消せます',
] as const

export const WORDS = {
  appName: 'アンサンブル',
  start: {
    title: '試す家庭を選ぶ',
    lead: '試作なので、架空の家庭で試します。',
    homes: [
      {
        id: 'home_a',
        label: '家庭Aで始める',
        note: '用事を頼める人は夫（夜と朝は動ける）。上の子は保育園。祖父母は遠い。',
      },
      {
        id: 'home_b',
        label: '家庭Bで始める',
        note: 'あなたのほかに、病室に泊まれる人がいない。夫は帰りが遅く不規則。延長保育とファミサポに登録済み。',
      },
    ],
    failed: 'ボードを作れませんでした。もう一度押してください',
  },
  add: {
    open: '＋入れる',
    title: '入れる',
    placeholder: '先生に言われたことを、そのまま。LINEは貼るだけ。',
    submit: '入れる',
    back: '戻る',
    over: (extra: number) => `2,000字までです。あと${extra}字減らしてください`,
    count: (length: number) => `${length} / 2,000字`,
  },
  consent: {
    title: 'はじめに',
    agree: 'わかった',
    close: '閉じる',
    open: '？',
    openLabel: '同意の内容を読む',
  },
  board: {
    loading: '読み込んでいます',
    notFound: 'ボードが見つかりません',
    startOver: '最初の画面へ',
    offline: '読み込めませんでした。つながってから、もう一度開いてください',
    retry: 'もう一度読み込む',
    sorting: '仕分け中',
    decide: 'いま決めること',
    decideNone: 'いま決めることはありません',
    events: '予定',
    emptyParts: (names: string[]) => `項目のない列：${names.join('、')}`,
    newMark: '新',
    done: '済',
    remove: '消す',
    removeAgain: 'もう一度押すと消します',
    failed: '保存できませんでした。つながってから、もう一度押してください',
    owner: '担当',
    due: '期日',
    location: 'ありか',
    original: '原文',
    speaker: (name: string) => `${name}より`,
  },
}

// 画面の列の順。台帳の part の値をそのまま見出しにする。
export const COLUMNS = ['病院', '家族', '保育園・預け先', '職場', '支援'] as const
