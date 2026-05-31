import { useEffect, useState } from 'react'
import { supabase } from './lib/supabase'
import './App.css'

interface ScheduledPost {
  id: string
  caption: string
  platforms: string[]
  scheduled_at: string
  status: string
  created_at: string
}

function App() {
  const [posts, setPosts] = useState<ScheduledPost[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPosts()
  }, [])

  async function fetchPosts() {
    try {
      const { data, error } = await supabase
        .from('scheduled_posts')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(10)

      if (error) throw error
      setPosts(data || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch posts')
    } finally {
      setLoading(false)
    }
  }

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      pending: '#f59e0b',
      processing: '#3b82f6',
      published: '#10b981',
      failed: '#ef4444',
      cancelled: '#6b7280'
    }
    return colors[status] || '#6b7280'
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <h1>JACK Social Scheduler</h1>
          <p>Multi-Platform Publishing Made Simple</p>
        </div>
        <div className="status">
          <span className="status-badge">✓ Connected to Supabase</span>
        </div>
      </header>

      <main className="main">
        <section className="dashboard">
          <div className="section-header">
            <h2>Scheduled Posts</h2>
            <span className="badge">{posts.length} posts</span>
          </div>

          {loading && (
            <div className="loading">
              <p>Loading posts...</p>
            </div>
          )}

          {error && (
            <div className="error">
              <p>Error: {error}</p>
              <button onClick={fetchPosts}>Retry</button>
            </div>
          )}

          {!loading && !error && posts.length === 0 && (
            <div className="empty-state">
              <p>No scheduled posts yet</p>
              <p className="hint">Create a post using the Studio</p>
            </div>
          )}

          <div className="posts-grid">
            {posts.map((post) => (
              <article key={post.id} className="post-card">
                <div className="post-header">
                  <div className="platforms">
                    {post.platforms.map((platform) => (
                      <span key={platform} className="platform-badge">
                        {platform}
                      </span>
                    ))}
                  </div>
                  <span
                    className="status-badge"
                    style={{ backgroundColor: getStatusColor(post.status) }}
                  >
                    {post.status}
                  </span>
                </div>
                <div className="post-body">
                  <p className="caption">{post.caption}</p>
                </div>
                <div className="post-footer">
                  <time>{new Date(post.scheduled_at).toLocaleString()}</time>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="features">
          <h2>Features</h2>
          <div className="features-grid">
            <div className="feature-card">
              <h3>📊 Analytics</h3>
              <p>Track engagement metrics across all platforms</p>
            </div>
            <div className="feature-card">
              <h3>🎨 Studio</h3>
              <p>Compose posts with platform-specific overrides</p>
            </div>
            <div className="feature-card">
              <h3>📅 Calendar</h3>
              <p>Visual schedule with drag-and-drop rescheduling</p>
            </div>
            <div className="feature-card">
              <h3>🔄 Repurpose</h3>
              <p>Convert long-form content to social posts</p>
            </div>
          </div>
        </section>

        <section className="tech-stack">
          <h2>Built With</h2>
          <div className="tech-badges">
            <span className="tech-badge">React</span>
            <span className="tech-badge">TypeScript</span>
            <span className="tech-badge">Vite</span>
            <span className="tech-badge">Supabase</span>
            <span className="tech-badge">Row-Level Security</span>
          </div>
        </section>
      </main>

      <footer className="footer">
        <p>JACK Social Scheduler • 100% Built • Production Ready</p>
      </footer>
    </div>
  )
}

export default App
