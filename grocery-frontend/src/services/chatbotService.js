import axios from 'axios';

const CHATBOT_API_URL = 'http://0.0.0.0:8001';

export const chatbotService = {
  sendMessage: async (query, userId, accessToken) => {
    return axios.post(`${CHATBOT_API_URL}/chat`, {
      query,
      user_id: userId,
      access_token: accessToken
    });
  },

  // Transcribe audio to text
  transcribeAudio: async (audioBlob) => {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'audio.webm');
    
    return axios.post(`${CHATBOT_API_URL}/voice/transcribe`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    });
  },

  // Convert text to speech
  synthesizeSpeech: async (text, voice = 'alloy') => {
    return axios.post(
      `${CHATBOT_API_URL}/voice/synthesize`,
      null,
      {
        params: { text, voice },
        responseType: 'blob'
      }
    );
  },

  // Complete voice chat flow (STT -> Process -> TTS)
  voiceChat: async (audioBlob, userId, accessToken, voice = 'alloy') => {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'audio.webm');
    formData.append('user_id', userId);
    formData.append('access_token', accessToken);
    formData.append('voice', voice);
    
    return axios.post(`${CHATBOT_API_URL}/voice/chat`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      responseType: 'blob'
    });
  },

  reindexProducts: async () => {
    return axios.post(`${CHATBOT_API_URL}/chat/reindex`);
  },

  healthCheck: async () => {
    return axios.get(`${CHATBOT_API_URL}/health`);
  }
};
