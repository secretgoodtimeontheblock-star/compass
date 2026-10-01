import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return (
      <div className="error" role="alert">
        <b>Не удалось показать этот раздел</b>
        <p>Повторите открытие. Сохранённые данные остаются в приложении.</p>
        <button className="btn" onClick={() => this.setState({ failed: false })}>Повторить</button>
      </div>
    );
    return this.props.children;
  }
}
