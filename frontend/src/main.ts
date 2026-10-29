import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { installMonitoring } from './monitoring'
import './styles/main.css'

const app = createApp(App)
installMonitoring(app)
app.use(router)
app.mount('#app')