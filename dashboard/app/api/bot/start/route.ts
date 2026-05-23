import { NextResponse } from 'next/server'

export async function POST() {
  return NextResponse.json(
    {
      ok: false,
      error: "Bot is managed by Docker — use 'docker start bot' from the CLI.",
    },
    { status: 503 }
  )
}
